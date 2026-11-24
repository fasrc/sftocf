"""signals for sftocf plugin"""

import django.dispatch

starfish_add_aduser = django.dispatch.Signal()
starfish_remove_aduser = django.dispatch.Signal()
starfish_add_adgroup = django.dispatch.Signal()
starfish_zone_created = django.dispatch.Signal()
    #providing_args=["project", "zone_id"]
    # sent when update_zones creates a new per-project Starfish zone

starfish_set_zone_userparam = django.dispatch.Signal()
    #providing_args=["zone_id", "param_name", "param_value"]
    # generic hook for setting a zone's user_param; callers (e.g. institution-
    # specific plugins) resolve zone_id/param_name/param_value themselves
