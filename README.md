# sftocf

Pip-installable [ColdFront](https://github.com/ubccr/coldfront) plugin that integrates Starfish storage usage and zones with ColdFront allocations. The Django app is imported as **`sftocf`** (`INSTALLED_APPS += ['sftocf']`).

## Install

Requires ColdFront in the same environment.

```bash
pip install sftocf
```

Editable install:

```bash
pip install -e .
```

## Enable

Add the plugin app and configuration in your ColdFront **`local_settings.py`** (or any settings module that loads after ColdFront’s base settings). The examples below use plain Python assignments; you can instead load values from your environment with `django-environ` or similar, as long as the resulting **Django settings** expose the same names.

### Required

| Setting | Description |
|--------|-------------|
| **`INSTALLED_APPS`** | Append the app: `INSTALLED_APPS += ['sftocf']`. |
| **`SFUSER`** | Starfish API username used for REST auth (`/api/auth/`). |
| **`SFPASS`** | Starfish API password. |
| **`STARFISH_URL`** | Base URL of your Starfish deployment **without a trailing slash** (e.g. `https://starfish.example.com`). Used to build `{STARFISH_URL}/api/` and `{STARFISH_URL}/redash/api/`. |

### Strongly recommended

| Setting | Description |
|--------|-------------|
| **`SF_VOLUME_MAPPING`** | JSON **string** mapping Starfish volume names to one or two path prefixes used by the REST collection pipeline (same shape as the historical `servers.json` `volumes` object). Example: `'{"vol1": ["LABS", "LABS"], "vol2": ["C/LABS", "F/LABS"]}'`. If missing or `{}`, REST path logic that relies on this map will not behave as intended. Read at import time via `import_from_settings('SF_VOLUME_MAPPING', '{}')`. |

### Required for Redash-based features

These features use **`StarFishRedash`** (e.g. `pull_sf_push_cf` with `--pulltype redash`, `import_allocation_filepaths`, parts of usage sync):

| Setting | Description |
|--------|-------------|
| **`REDASH_API_KEYS`** | A dict mapping **query name → `[query_id, redash_api_key]`**. Names used in code include at least: `'vol_query'`, `'path_usage_query'`, `'subdirectory'` (and any other keys passed to `submit_query` / `return_query_results` in your deployment). |

### Optional

| Setting | Default (if unset) | Description |
|--------|---------------------|-------------|
| **`SFTOCF_DATAPATH`** | Package `data/` directory | Writable directory for REST pipeline JSON cache files (should end with a path separator consistent with your OS or how paths are joined). |
| **`PENDING_ACTIVE_ALLOCATION_STATUSES`** | `['Active', 'New', 'In Progress', 'On Hold']` | Allocation `status__name` values treated as “pending/active” for usage pipelines. |
| **`username_ignore_list`** | `[]` | Usernames excluded when reconciling Starfish users with ColdFront (via shared `coldfront.core.utils.fasrc` helpers). |
| **`CENTER_BASE_URL`** | `''` | Base URL of your ColdFront site (no trailing slash), e.g. `https://coldfront.example.com`. Used in `import_allocation_filepaths` error payloads as `{CENTER_BASE_URL}/allocation/<pk>`. If empty, those URLs are relative-looking (`/allocation/...`). |

### Logging (optional)

To match the default file logging for this app, extend **`LOGGING`** in `local_settings.py`, for example:

```python
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

Adjust `filename` to a path your deployment can write.

### Environment toggle (stock ColdFront split-settings)

If you use ColdFront’s **`PLUGIN_SFTOCF`** env flag and the built-in plugin include list, you can keep using `coldfront.config.plugins.sftocf` to load settings from a file. If you **only** use `local_settings.py`, add the variables and `INSTALLED_APPS` there and you do not need `PLUGIN_SFTOCF` or the `sftocf.coldfront_settings` import—unless you still want that include for consistency.

### Reference: `sftocf/coldfront_settings.py`

The package still ships `sftocf/coldfront_settings.py` as a **reference** fragment (same keys as above, using `django-environ`’s `ENV` for `SFUSER`, `SFPASS`, `STARFISH_URL`, `SF_VOLUME_MAPPING`). You can copy its contents into `local_settings.py` and adapt paths or logging as needed.

### Minimal `local_settings.py` sketch

```python
INSTALLED_APPS += ['sftocf']

SFUSER = '...'
SFPASS = '...'
STARFISH_URL = 'https://starfish.example.com'  # no trailing slash
SF_VOLUME_MAPPING = '{"myvol": ["LABS", "LABS"]}'  # JSON string

# Redash: only if you use Redash-backed commands / pipelines
REDASH_API_KEYS = {
    'vol_query': [123, 'redash-api-key'],
    'path_usage_query': [456, 'redash-api-key'],
    'subdirectory': [789, 'redash-api-key'],
}

# Optional
# SFTOCF_DATAPATH = '/var/lib/sftocf/data/'
# CENTER_BASE_URL = 'https://coldfront.example.com'
```

## Layout

- `sftocf/` — Python package (Django app).

## Build

```bash
python -m pip install build
python -m build
```

## Tests

```bash
python manage.py test sftocf
```
