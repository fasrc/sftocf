from django.apps import AppConfig


class StarFishConfig(AppConfig):
    name = 'sftocf'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import sftocf.signals  # noqa: F401
