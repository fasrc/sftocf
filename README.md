# sftocf
A Coldfront plugin for pulling usage data from Starfish.

This plugin provides a command for collecting usage data from the Starfish API at
the project user level and inserting the data into the Coldfront database. It
also contains DjangoQ integration options that make it easy to automatically schedule
pulls.


## Installation

Right now, installation consists of adding this directory to Coldfront's `plugins`
directory and following the directions for configuration.

## Configuration

To enable this plugin:
1. Set the following environment variables:

```
PLUGIN_SFTOCF=True
SFUSER='starfish_username'
SFPASS='starfish_password'
```

2. In `coldfront/config/plugins/`, create file `sftocf.py` with the following contents:

```py
from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV

INSTALLED_APPS += [ 'coldfront.plugins.sf_to_cf' ]

SFUSER = ENV.str('SFUSER')
SFPASS = ENV.str('SFPASS')
```

3. In `coldfront.config.settings`, ensure that `'PLUGIN_SFTOCF': 'plugins/sftocf.py',`
is in the `plugin_configs` dictionary.

4. In the Coldfront `.env` file, add the following line: 

`PLUGIN_SFTOCF=True`

5. Fill out the `servers.json` template with the servers and accompanying urls and
volume names that correspond to your Starfish setup. The plugin uses this file to
identify the servers and volumes to collect from the Starfish API.


## Usage

Upon installation, sftocf will add the `pull_sf_push_cf` command to your
`./manage.py` menu. Running `./manage.py pull_sf_push_cf` will pull usage data for
project users and save it as JSON in the plugin's data directory, then push that
data into the Coldfront database. The command can be set to only update one
volume (as pulling the Starfish data can take a long time) using the `volume`
parameter and to delete the JSON files after it has successfully inserted the
data using the `clean` parameter.


### DjangoQ Integration

You can schedule your Starfish data pull using DjangoQ by:

A. Going to "scheduled tasks" in the adminland DjangoQ section and adding a task
with the func value of `coldfront.plugins.sftocf.tasks.pull_sf_push_cf` (and then
adding any further )

B. adding the following code to
`coldfront/core/utils/management/commands/add_scheduled_tasks.py` and then running
the add_scheduled_tasks command:

        ```py
        # adds a task scheduled to run weekly
        if 'coldfront.plugins.sftocf' in settings.INSTALLED_APPS:
            schedule('coldfront.plugins.sftocf.tasks.pull_sf_push_cf',
                    schedule_type=Schedule.WEEKLY,
                    repeats=-1,
                    next_run=timezone.now() + datetime.timedelta(days=1))
        ```
