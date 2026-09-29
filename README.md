# GitLab Migration Tool

Python script to automatically migrate hundreds of repositories from one GitLab instance to another, preserving the hierarchy and all branches/tags.

## Features

- ✅ Migration of all repositories of a group/namespace (including subgroups)
- ✅ Preservation of the group/subgroup hierarchy
- ✅ Migration of all branches and tags
- ✅ Git LFS (Large File Storage) support
- ✅ Automatic creation of missing groups
- ✅ Protection: never touches existing projects
- ✅ Detailed logging with a migration report
- ✅ Robust error handling

## Prerequisites

- Python 3.7+
- Git installed on the machine
- Git LFS installed (if your repositories use LFS)
- Enough disk space for the temporary clones
- Personal access tokens on both GitLab instances

## Installation

*It is recommended that you use a Python virtual environment.*


1. **Install the Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create the GitLab tokens:**

   **On the source instance:**
   - Go to Settings → Access Tokens
   - Create a token with the scopes: `read_api`, `read_repository`

   **On the destination instance:**
   - Go to Settings → Access Tokens
   - Create a token with the scopes: `api`, `write_repository`

3. **Configure the environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

## Configuration (.env)

```env
# Source GitLab instance
SOURCE_GITLAB_URL=https://old-gitlab.example.com
SOURCE_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# Destination GitLab instance
DEST_GITLAB_URL=https://new-gitlab.example.com
DEST_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# Source group path (can include subgroups)
SOURCE_GROUP_PATH=my-group/my-subgroup

# OR a source username (personal projects, not in a group)
# SOURCE_USER=jdoe

# Destination group path (or a username for a personal namespace)
DEST_GROUP_PATH=new-group
```

Set **exactly one** of `SOURCE_GROUP_PATH` or `SOURCE_USER`.

### Configuration examples

**Example 1: Simple migration**
```env
SOURCE_GROUP_PATH=old-team
DEST_GROUP_PATH=new-team
```
Result: `old-team/project1` → `new-team/project1`

**Example 2: Hierarchy preservation**
```env
SOURCE_GROUP_PATH=old-team
DEST_GROUP_PATH=new-team
```
If the source contains: `old-team/backend/api/service1`
Result: `new-team/backend/api/service1`

**Example 3: To your personal namespace**
```env
DEST_GROUP_PATH=my-username
```
The namespace (group or user) is detected automatically. A user namespace cannot contain subgroups. To create projects in another user's namespace, the destination token must belong to an admin.

**Example 4: Migrating the personal repositories of a user**
```env
SOURCE_USER=jdoe
DEST_GROUP_PATH=new-group
```
Result: `jdoe/project1` → `new-group/project1`. With a non-admin source token, only the projects visible to that token are listed.

## Usage

```bash
python migrate_gitlab.py
```

The script will:
1. Connect to both GitLab instances
2. List all projects of the source group (including subgroups) or of the source user
3. Display the projects found
4. Ask for confirmation before starting
5. For each project, ask for confirmation (`y` yes, `n` skip, `a` migrate all remaining without asking again, `q` quit), then:
   - Check whether it already exists (skip if so)
   - Create the group hierarchy if needed
   - Create the project on the destination
   - Clone locally (with LFS if applicable)
   - Push in mirror mode to the destination
   - Clean up the local clone
6. Display a migration report

## Logs

A log file is created automatically: `migration_YYYYMMDD_HHMMSS.log`

It contains:
- All operations performed
- Successfully migrated projects
- Skipped projects (already existing)
- Errors encountered

## Error handling

- **Project already exists**: The script skips it automatically (protection)
- **Clone error**: Logged, then moves on to the next project
- **Push error**: Logged, then moves on to the next project
- **Interruption (Ctrl+C)**: Displays the report of the projects already migrated

## Resuming after an error

The script can be safely re-run:
- Projects that were already migrated are automatically skipped
- Only projects not yet migrated or in error are processed

## Code structure

```
migrate_gitlab.py          # Main script
requirements.txt           # Python dependencies
.env                       # Configuration (to be created)
.env.example               # Configuration template
README.md                  # This documentation
migration_*.log            # Logs generated at each run
temp_migration/            # Temporary folder (cleaned up automatically)
```

## Security

- ⚠️ **Never commit your `.env` file** (it contains sensitive tokens)
- Tokens are only used locally
- Temporary clones are cleaned up after each migration
- Default visibility: `private` for all new projects

## Troubleshooting

**"Group not found"**
- Check that `SOURCE_GROUP_PATH` exists and is accessible
- Check the source token permissions

**"Permission denied"**
- Check that the destination token has the `api` and `write_repository` scopes
- Check that you have the right to create projects in the destination group

**"Clone failed"**
- Check the available disk space
- Check that Git is installed and accessible

**"LFS errors"**
- Check that Git LFS is installed: `git lfs version`
- Install it if needed: https://git-lfs.com/

**"Rate limit exceeded"**
- Add pauses between migrations (modify the code if necessary)
- Contact the GitLab admin to raise the limits

## Limitations

- Only the Git code is migrated (not issues, merge requests, wikis, etc.)
- Project permissions/members are not copied
- CI/CD variables are not migrated
- Webhooks are not migrated

## Performance

For 300 repositories:
- Duration: depends on repository size (typically a few hours)
- Disk space: plan for ~2x the size of the largest repository
- The script migrates projects one by one (sequentially)

## Support

For any question or problem, check:
1. The detailed logs in `migration_*.log`
2. The `.env` configuration
3. The GitLab token permissions

## Sample output

```
2024-01-15 10:30:00 - INFO - Connected to source: https://old-gitlab.com
2024-01-15 10:30:01 - INFO - Connected to destination: https://new-gitlab.com
2024-01-15 10:30:02 - INFO - Found 300 projects in source

Found 300 projects to migrate:
  - old-team/backend/api/service1
  - old-team/broken-repo
  [...]

Do you want to continue? (yes/no): yes

Migrate old-team/backend/api/service1? [y]es / [n]o / [a]ll remaining / [q]uit: y

================================================================================
Processing: old-team/backend/api/service1
Destination: new-team/backend/api/service1
Creating group: new-team/backend/api
Creating project on destination...
Cloning old-team/backend/api/service1...
Pushing to destination...
✓ Successfully migrated: old-team/backend/api/service1
================================================================================

[...]

================================================================================
Migration Summary
================================================================================
Total projects: 300
Successfully migrated: 295
Skipped (already exist): 3
Declined by user: 0
Failed: 2

Errors:
  - old-team/broken-repo: Clone failed
  - old-team/huge-repo: Push failed: timeout
================================================================================
```
