"""Settings and environment-derived configuration for the sftocf plugin."""
import json
import os
from datetime import datetime
from pathlib import Path

from coldfront.core.utils.common import import_from_settings

_PLUGIN_ROOT = Path(__file__).resolve().parent

DATESTR = datetime.today().strftime('%Y%m%d')
_DEFAULT_DATAPATH = str(_PLUGIN_ROOT / 'data') + os.sep
DATAPATH = import_from_settings('SFTOCF_DATAPATH', _DEFAULT_DATAPATH)
SFURL = import_from_settings('SFURL', 'starfish')
PENDING_ACTIVE_ALLOCATION_STATUSES = import_from_settings(
    'PENDING_ACTIVE_ALLOCATION_STATUSES', ['Active', 'New', 'In Progress', 'On Hold'])
ASYNC_QUERY_TIMEOUT_SECONDS = import_from_settings('SF_ASYNC_QUERY_TIMEOUT_SECONDS', 3600)

svp = json.loads(import_from_settings('SF_VOLUME_MAPPING', '{}'))

username_ignore_list = import_from_settings('username_ignore_list', [])

os.makedirs(DATAPATH, exist_ok=True)
