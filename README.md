# sftocf

Pip-installable [ColdFront](https://github.com/ubccr/coldfront) plugin that integrates Starfish storage usage and zones with ColdFront allocations. The Django app is imported as **`sftocf`** (`INSTALLED_APPS += ['sftocf']`).

Source code and issue tracker: **[github.com/fasrc/sftocf](https://github.com/fasrc/sftocf)**.

## Install

You need **ColdFront** and **sftocf** in the same Python environment (same virtualenv or container image as your ColdFront deployment).

### From GitHub (recommended today)

Install the latest default branch into your environment:

```bash
pip install "git+https://github.com/fasrc/sftocf.git"
```

Pin a branch, tag, or commit for reproducible deploys:

```bash
pip install "git+https://github.com/fasrc/sftocf.git@main"
# or: .../sftocf.git@<tag-or-commit>
```

### From a local clone (development)

```bash
git clone https://github.com/fasrc/sftocf.git
cd sftocf
pip install -e .
```

### From PyPI (if published)

When a release is published to the Python Package Index:

```bash
pip install sftocf
```

## Enable

Add the plugin app and configuration in your ColdFront **`local_settings.py`** (or any settings module that loads after ColdFront’s base settings). The examples below use plain Python assignments; you can instead load values from your environment with `django-environ` or similar, as long as the resulting **Django settings** expose the same names.

### Example `local_settings.py` sketch

```python
from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV
from coldfront.config.logging import LOGGING

##### SFTOCF PLUGIN SETTINGS #####

### Required ###
INSTALLED_APPS += ['sftocf']

# using env vars to set sensitive information is recommended
SFUSER = ENV.str('SFUSER', default='coldfront')
SFPASS = ENV.str('SFPASS', default='my_password')
SFURL = ENV.str('SFURL', default='https://starfish-instance.myuniversity.edu')

### For REST API ###
SF_VOLUME_MAPPING = '{"myvol": ["LABS", "LABS"]}'  # JSON string

### For Redash-backed commands / pipelines ###
REDASH_API_KEYS = {
    'vol_query': [123, 'redash-api-key'],
    'path_usage_query': [456, 'redash-api-key'],
    'subdirectory': [789, 'redash-api-key'],
}

### Optional ###
# SFTOCF_DATAPATH = '/var/lib/sftocf/data/'
# CENTER_BASE_URL = 'https://coldfront.example.com'
# SFTOCF_IGNORED_GROUP_NAMES = ['non_project_group', 'other_noise']  # id_new_storage_allocations

# Log settings
LOGGING['handlers']['sftocf'] = {
    'class': 'logging.handlers.TimedRotatingFileHandler',
    'filename': 'logs/sftocf.log',
    'when': 'D',
    'backupCount': 10,
    'formatter': 'default',
    'level': 'DEBUG',
}
LOGGING['loggers']['sftocf'] = {
    'handlers': ['sftocf'],
}
```

### Settings reference

Use the **Tier** column to see what applies to your deployment. **Required if using Redash** applies whenever you use **`StarFishRedash`** (for example `pull_sf_push_cf` with `--pulltype redash`, `import_allocation_filepaths`, parts of usage sync, and `id_new_storage_allocations`).

| Setting | Tier | Default (if unset) | Description |
|---------|------|--------------------|-------------|
| **`INSTALLED_APPS`** | Required | — | Append `'sftocf'` (see sketch). |
| **`SFUSER`** | Required | — | Starfish API username for REST auth (`/api/auth/`). |
| **`SFPASS`** | Required | — | Starfish API password. |
| **`SFURL`** | Required | `starfish` (placeholder) | Base URL of your Starfish deployment **without a trailing slash** (e.g. `https://starfish.example.com`). Used to build `{SFURL}/api/` and `{SFURL}/redash/api/`. Read via `import_from_settings('SFURL', 'starfish')`. |
| **`SF_VOLUME_MAPPING`** | Strongly recommended | `'{}'` | JSON **string** mapping Starfish volume names to one or two path prefixes for the REST collection pipeline. Example: `'{"vol1": ["LABS", "LABS"], "vol2": ["C/LABS", "F/LABS"]}'`. If empty, REST path logic that relies on this map will not behave as intended. Read at import time via `import_from_settings('SF_VOLUME_MAPPING', '{}')`. |
| **`REDASH_API_KEYS`** | Required if using Redash | — | Dict mapping **query name → `[query_id, redash_api_key]`**. Names used in code include at least: `'vol_query'`, `'path_usage_query'`, `'subdirectory'` (and any other keys your deployment passes to `submit_query` / `return_query_results`). |
| **`SFTOCF_DATAPATH`** | Optional | Package `data/` directory | Writable directory for REST pipeline JSON cache files (prefer a trailing separator consistent with how paths are joined on your OS). |
| **`PENDING_ACTIVE_ALLOCATION_STATUSES`** | Optional | `['Active', 'New', 'In Progress', 'On Hold']` | Allocation `status__name` values treated as “pending/active” for usage pipelines. |
| **`username_ignore_list`** | Optional | `[]` | Usernames excluded when reconciling Starfish users with ColdFront (via shared `coldfront.core.utils.fasrc` helpers). |
| **`CENTER_BASE_URL`** | Optional | `''` | Base URL of your ColdFront site (no trailing slash), e.g. `https://coldfront.example.com`. Used in `import_allocation_filepaths` error payloads as `{CENTER_BASE_URL}/allocation/<pk>`. If empty, those URLs look relative (`/allocation/...`). |
| **`SFTOCF_IGNORED_GROUP_NAMES`** | Optional | `[]` | Starfish **group names** (`group_name` from the Redash `subdirectory` query) excluded by **`manage.py id_new_storage_allocations`** when building `local_data/new_allocations.csv`. Rows whose `group_name` contains **`DISABLED`** are always dropped. `import_from_settings('SFTOCF_IGNORED_GROUP_NAMES', [])`. |
| **`LOGGING`** (sftocf handler/logger) | Optional | — | Extend Django’s **`LOGGING`** dict with a file handler and a logger for the `sftocf` namespace; see [Logging (optional)](#logging-optional). |

### Using sftocf with Redash

sftocf can collect the output of Redash queries to collect information that would take a long time to gather and process if using the REST API.

Example Redash SQL used with the keys in **`REDASH_API_KEYS`**:

#### `vol_query`
```sql
SELECT
    volume.id AS "id",
    volume.name AS "volume_name",
    ROUND("total capacity" / POWER(1024.0, 4), 2) AS "capacity_TB",
    ROUND("volume occupied space" / POWER(1024.0, 4), 2) AS "used_physical_TB",
    ROUND("volume occupied space logical" / POWER(1024.0, 4), 2) AS "used_logical_TB",
    ROUND("volume free space" / POWER(1024.0, 4), 2) AS "free_TB",
    "volume occupied space %" AS "used_percent",
    "regular files" AS "regular_files",
    "median file size" AS "median_file_size_bytes",
    ROUND("average file size")::BIGINT AS "average_file_size_bytes"
FROM sf_reports.stats_current
    LEFT JOIN sf_volumes.volume AS volume ON stats_current."volume name" = volume.name
```


#### `subdirectory`
```sql
/* Return aggregate size of directories that correspond to storage allocations. */
SELECT 
    dirs.volume_id, 
    vol.name AS vol_name, 
    dirs.gid, 
    vol_group.name AS group_name, 
    dirs.uid, 
    vol_user.name AS user_name, 
    dirs.path AS path, 
    (dirs.rec_aggrs->>'size')::numeric AS total_size, 
    dirs.depth
FROM 
    sf.dir_current dirs
LEFT JOIN 
    sf_volumes.volume vol ON dirs.volume_id = vol.id
LEFT JOIN 
    sf.uid_mapping vol_user ON dirs.volume_id = vol_user.volume_id AND dirs.uid = vol_user.uid
LEFT JOIN 
    sf.gid_mapping vol_group ON dirs.volume_id = vol_group.volume_id AND dirs.gid = vol_group.gid
WHERE
    /* change these criteria to correspond to storage allocations' root directories */
    LENGTH(vol_group.name) > 1
    AND dirs.path SIMILAR TO 'LABS/%'
    AND dirs.depth = 2
ORDER BY 
    volume_id, path;
```

#### `path_usage_query`
```sql
/*
Calculate usage per user per allocation directory.

Collect user, size, volume, and allocation path of any files in dirs that contain
an allocation path in its path value, then aggregate by user+path+volume.
*/

SELECT 
    user_usage.size_sum,
    user_usage.lab_path,
    vol.name AS vol_name,
    vol_user.name AS user_name
FROM (
    SELECT 
        SUM(size) AS size_sum,
        lab_path,
        volume_id,
        uid
    FROM (
        SELECT 
            dirs.path, 
            substring(dirs.path FROM '(LABS/[^/]+)') AS lab_path,
            files.size,
            files.volume_id,
            files.gid, 
            files.uid
        FROM sf.dir_current dirs
        JOIN sf.file_current files ON dirs.id = files.parent_id
        WHERE
            NOT files.size = 0 
            AND (dirs.path LIKE 'LABS/%' AND dirs.depth > 2)
    ) userfiles
    -- group by user, volume, lab_dirs path
    GROUP BY uid, lab_path, volume_id) user_usage
LEFT JOIN sf_volumes.volume vol ON user_usage.volume_id = vol.id
LEFT JOIN sf.uid_mapping vol_user ON user_usage.volume_id = vol_user.volume_id AND user_usage.uid = vol_user.uid
```

### Django-Q scheduled tasks

Stock ColdFront’s `add_scheduled_tasks` management command **does not** register `sftocf` jobs. After installing this package, register schedules yourself (django-q `Schedule` UI, migrations, or shell), e.g. task paths:

- `sftocf.tasks.pull_sf_push_cf`
- `sftocf.tasks.update_zones`
- `sftocf.tasks.import_allocation_filepaths`


## Layout

- `sftocf/` — Python package (Django app).

## Build

```bash
python -m pip install build
python -m build
```

## Tests

Run from your ColdFront project (with `sftocf` in `INSTALLED_APPS`):

```bash
python manage.py test sftocf
```

- **`sftocf.tests.test_utils_helpers`** — pure helpers and `AllocationQueryMatch` (no DB).
- **`sftocf.tests.test_starfish_server`** — `StarFishServer` init/auth with mocks.
- **`sftocf.tests.test_signals`** — Starfish Django signals.
- **`sftocf.tests.test_integration`** — optional DB tests using ColdFront factories (requires `coldfront.core.test_helpers` and fixtures).

JSON samples for manual checks live under `sftocf/fixture_data/`.
