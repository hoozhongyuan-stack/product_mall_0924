"""Deploy hook: create a private, immutable local source snapshot."""

from django.core.management.base import BaseCommand, CommandError

from code_versions.package import PackageError
from code_versions.service import CodeBuildError, build_local_version


class Command(BaseCommand):
    help = "Build and persist the deployment Mini Program source snapshot locally (no WeChat upload)."

    def handle(self, *args, **options):
        try:
            version = build_local_version()
        except (PackageError, CodeBuildError) as exc:
            raise CommandError(f"Mini Program local source snapshot failed: {exc.code}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Local Mini Program source snapshot ready: {version.version_label} ({version.package_sha256})"))
