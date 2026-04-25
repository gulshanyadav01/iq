from django.apps import AppConfig


class CommissionIntakeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commission_intake"

    def ready(self):
        import commission_intake.signals  # noqa: F401
