from django.apps import AppConfig


class BenefitsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "benefits"

    def ready(self):
        from .signals import connect_member_account_signal

        connect_member_account_signal()
