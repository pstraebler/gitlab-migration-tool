#!/usr/bin/env python3
"""
GitLab Migration Script
Migrates repositories from one GitLab instance to another, preserving hierarchy.
"""

import os
import sys
import subprocess
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import gitlab
from dotenv import load_dotenv


class GitLabMigrator:
    def __init__(self):
        load_dotenv()

        # Configuration
        self.source_url = os.getenv('SOURCE_GITLAB_URL')
        self.source_token = os.getenv('SOURCE_GITLAB_TOKEN')
        self.dest_url = os.getenv('DEST_GITLAB_URL')
        self.dest_token = os.getenv('DEST_GITLAB_TOKEN')
        self.source_group_path = os.getenv('SOURCE_GROUP_PATH')
        self.source_user = os.getenv('SOURCE_USER')
        self.dest_group_path = os.getenv('DEST_GROUP_PATH')

        self._validate_config()

        # Namespace prefix of source projects (group path or username)
        self.source_prefix = self.source_user or self.source_group_path

        # Setup logging
        self.setup_logging()

        # Connect to GitLab instances
        self.source_gl = gitlab.Gitlab(self.source_url, private_token=self.source_token)
        self.dest_gl = gitlab.Gitlab(self.dest_url, private_token=self.dest_token)

        # Test connections
        self.source_gl.auth()
        self.dest_gl.auth()

        self.logger.info(f"Connected to source: {self.source_url}")
        self.logger.info(f"Connected to destination: {self.dest_url}")

        # Stats
        self.stats = {
            'total': 0,
            'success': 0,
            'skipped': 0,
            'declined': 0,
            'failed': 0,
            'errors': []
        }

        # Temporary directory for clones
        self.temp_dir = Path('./temp_migration')
        self.temp_dir.mkdir(exist_ok=True)

    def _validate_config(self):
        required = [
            'SOURCE_GITLAB_URL', 'SOURCE_GITLAB_TOKEN',
            'DEST_GITLAB_URL', 'DEST_GITLAB_TOKEN',
            'DEST_GROUP_PATH'
        ]
        missing = [var for var in required if not os.getenv(var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        if bool(self.source_group_path) == bool(self.source_user):
            raise ValueError("Set exactly one of SOURCE_GROUP_PATH or SOURCE_USER")

    def setup_logging(self):
        log_file = f"migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Logging to {log_file}")

    def get_source_projects(self) -> List:
        """List all projects of the source user, or of the source group recursively."""
        if self.source_user:
            return self.get_user_projects()

        self.logger.info(f"Fetching projects from source group: {self.source_group_path}")

        try:
            group = self.source_gl.groups.get(self.source_group_path)
            projects = group.projects.list(include_subgroups=True, all=True)
            self.logger.info(f"Found {len(projects)} projects in source")
            return projects
        except gitlab.exceptions.GitlabGetError:
            self.logger.error(f"Group not found: {self.source_group_path}")
            raise

    def get_user_projects(self) -> List:
        """List the personal projects (not in a group) of the source user."""
        self.logger.info(f"Fetching personal projects of source user: {self.source_user}")

        users = self.source_gl.users.list(username=self.source_user)
        if not users:
            self.logger.error(f"User not found: {self.source_user}")
            raise ValueError(f"User not found: {self.source_user}")

        projects = users[0].projects.list(all=True)
        self.logger.info(f"Found {len(projects)} projects in source")
        return projects

    def get_or_create_group(self, group_path: str) -> Optional[int]:
        """Get or create a destination namespace (group/subgroup or user), creating parent groups as needed."""
        # namespaces.get resolves both groups and user namespaces by full path
        try:
            namespace = self.dest_gl.namespaces.get(group_path)
            self.logger.debug(f"Namespace exists ({namespace.kind}): {group_path}")
            return namespace.id
        except gitlab.exceptions.GitlabGetError:
            pass

        # Group doesn't exist, create it
        parts = group_path.split('/')

        # Build hierarchy from root to leaf
        for i in range(len(parts)):
            current_path = '/'.join(parts[:i+1])
            try:
                group = self.dest_gl.groups.get(current_path)
            except gitlab.exceptions.GitlabGetError:
                # Create this level
                parent_id = None
                if i > 0:
                    parent_path = '/'.join(parts[:i])
                    parent_group = self.dest_gl.groups.get(parent_path)
                    parent_id = parent_group.id

                self.logger.info(f"Creating group: {current_path}")
                group_data = {
                    'name': parts[i],
                    'path': parts[i],
                    'visibility': 'private'
                }
                if parent_id:
                    group_data['parent_id'] = parent_id

                group = self.dest_gl.groups.create(group_data)

        return group.id

    def compute_destination_path(self, source_project) -> str:
        """Compute the destination group path for a project."""
        # Get full project path
        project_path_with_namespace = source_project.path_with_namespace

        # Remove the source group prefix
        if project_path_with_namespace.startswith(self.source_prefix + '/'):
            relative_path = project_path_with_namespace[len(self.source_prefix) + 1:]
        else:
            relative_path = project_path_with_namespace

        # Split into group path and project name
        parts = relative_path.split('/')
        if len(parts) > 1:
            # Has subgroups
            project_name = parts[-1]
            subgroup_path = '/'.join(parts[:-1])
            dest_group = f"{self.dest_group_path}/{subgroup_path}"
        else:
            # No subgroups
            project_name = parts[0]
            dest_group = self.dest_group_path

        return dest_group, project_name

    def project_exists(self, group_path: str, project_name: str) -> bool:
        """Check if a project already exists at destination."""
        full_path = f"{group_path}/{project_name}"
        try:
            self.dest_gl.projects.get(full_path)
            return True
        except gitlab.exceptions.GitlabGetError:
            return False

    def clone_repository(self, source_project, clone_path: Path) -> bool:
        """Clone repository with all branches, tags, and LFS."""
        clone_url = source_project.http_url_to_repo

        # Add token to URL for authentication
        clone_url = clone_url.replace('https://', f'https://oauth2:{self.source_token}@')

        self.logger.info(f"Cloning {source_project.path_with_namespace}...")

        try:
            # Clone bare repository
            subprocess.run(
                ['git', 'clone', '--bare', clone_url, str(clone_path)],
                check=True,
                capture_output=True,
                text=True
            )

            # Check if LFS is used
            lfs_check = subprocess.run(
                ['git', '-C', str(clone_path), 'lfs', 'ls-files'],
                capture_output=True,
                text=True
            )

            if lfs_check.stdout.strip():
                self.logger.info(f"LFS detected, fetching LFS objects...")
                subprocess.run(
                    ['git', '-C', str(clone_path), 'lfs', 'fetch', '--all'],
                    check=True,
                    capture_output=True,
                    text=True
                )

            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Clone failed: {e.stderr}")
            return False

    def push_to_destination(self, clone_path: Path, dest_project_url: str) -> bool:
        """Push all branches and tags to destination."""
        # Add token to URL
        push_url = dest_project_url.replace('https://', f'https://oauth2:{self.dest_token}@')

        self.logger.info(f"Pushing to destination...")

        try:
            # Push mirror (all refs)
            subprocess.run(
                ['git', '-C', str(clone_path), 'push', '--mirror', push_url],
                check=True,
                capture_output=True,
                text=True
            )

            # Push LFS objects if any
            lfs_check = subprocess.run(
                ['git', '-C', str(clone_path), 'lfs', 'ls-files'],
                capture_output=True,
                text=True
            )

            if lfs_check.stdout.strip():
                self.logger.info(f"Pushing LFS objects...")
                subprocess.run(
                    ['git', '-C', str(clone_path), 'lfs', 'push', '--all', push_url],
                    check=True,
                    capture_output=True,
                    text=True
                )

            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Push failed: {e.stderr}")
            return False

    def migrate_project(self, source_project):
        """Migrate a single project."""
        project_name = source_project.path_with_namespace
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"Processing: {project_name}")
        self.stats['total'] += 1

        try:
            # Compute destination path
            dest_group_path, dest_project_name = self.compute_destination_path(source_project)
            self.logger.info(f"Destination: {dest_group_path}/{dest_project_name}")

            # Check if already exists
            if self.project_exists(dest_group_path, dest_project_name):
                self.logger.warning(f"Project already exists, skipping: {dest_group_path}/{dest_project_name}")
                self.stats['skipped'] += 1
                return

            # Create destination group hierarchy
            group_id = self.get_or_create_group(dest_group_path)

            # Create project on destination
            self.logger.info(f"Creating project on destination...")
            dest_project = self.dest_gl.projects.create({
                'name': source_project.name,
                'path': dest_project_name,
                'namespace_id': group_id,
                'visibility': 'private',
                'description': source_project.description or ''
            })

            # Clone repository locally
            clone_path = self.temp_dir / f"{source_project.id}"
            if not self.clone_repository(source_project, clone_path):
                raise Exception("Clone failed")

            # Push to destination
            if not self.push_to_destination(clone_path, dest_project.http_url_to_repo):
                raise Exception("Push failed")

            # Cleanup
            shutil.rmtree(clone_path, ignore_errors=True)

            self.logger.info(f"✓ Successfully migrated: {project_name}")
            self.stats['success'] += 1

        except Exception as e:
            self.logger.error(f"✗ Failed to migrate {project_name}: {str(e)}")
            self.stats['failed'] += 1
            self.stats['errors'].append({
                'project': project_name,
                'error': str(e)
            })

    def ask_project_confirmation(self, project) -> str:
        """Ask whether to migrate a project. Returns 'yes', 'no', 'all' or 'quit'."""
        choices = {
            'y': 'yes', 'yes': 'yes',
            'n': 'no', 'no': 'no',
            'a': 'all', 'all': 'all',
            'q': 'quit', 'quit': 'quit',
        }
        while True:
            answer = input(
                f"\nMigrate {project.path_with_namespace}? [y]es / [n]o / [a]ll remaining / [q]uit: "
            ).strip().lower()
            if answer in choices:
                return choices[answer]
            print("Please answer y, n, a or q.")

    def run(self):
        """Run the migration."""
        self.logger.info(f"\n{'='*80}")
        self.logger.info("Starting GitLab Migration")
        if self.source_user:
            self.logger.info(f"Source: personal projects of user {self.source_user}")
        else:
            self.logger.info(f"Source: {self.source_group_path}")
        self.logger.info(f"Destination: {self.dest_group_path}")
        self.logger.info(f"{'='*80}\n")

        try:
            # Get all projects
            projects = self.get_source_projects()

            if not projects:
                self.logger.warning("No projects found to migrate")
                return

            # Confirm migration
            print(f"\nFound {len(projects)} projects to migrate:")
            for project in projects:
                print(f"  - {project.path_with_namespace}")
            response = input("\nDo you want to continue? (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                self.logger.info("Migration cancelled by user")
                return

            # Migrate each project, asking for confirmation unless "all" was chosen
            confirm_each = True
            for project in projects:
                if confirm_each:
                    answer = self.ask_project_confirmation(project)
                    if answer == 'quit':
                        self.logger.info("Migration stopped by user")
                        break
                    if answer == 'no':
                        self.logger.info(f"Declined by user, skipping: {project.path_with_namespace}")
                        self.stats['declined'] += 1
                        continue
                    if answer == 'all':
                        confirm_each = False
                self.migrate_project(project)

            # Print summary
            self.print_summary()

        except KeyboardInterrupt:
            self.logger.warning("\n\nMigration interrupted by user")
            self.print_summary()
        except Exception as e:
            self.logger.error(f"Migration failed: {str(e)}")
            raise
        finally:
            # Cleanup temp directory
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)

    def print_summary(self):
        """Print migration summary."""
        self.logger.info(f"\n{'='*80}")
        self.logger.info("Migration Summary")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"Total projects: {self.stats['total']}")
        self.logger.info(f"Successfully migrated: {self.stats['success']}")
        self.logger.info(f"Skipped (already exist): {self.stats['skipped']}")
        self.logger.info(f"Declined by user: {self.stats['declined']}")
        self.logger.info(f"Failed: {self.stats['failed']}")

        if self.stats['errors']:
            self.logger.info(f"\nErrors:")
            for error in self.stats['errors']:
                self.logger.info(f"  - {error['project']}: {error['error']}")

        self.logger.info(f"{'='*80}\n")


if __name__ == '__main__':
    try:
        migrator = GitLabMigrator()
        migrator.run()
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
        sys.exit(1)
