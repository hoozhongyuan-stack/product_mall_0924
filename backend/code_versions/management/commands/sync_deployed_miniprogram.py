"""Fail-closed deployment step for a pinned, committed Mini Program tree."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from code_versions.package import PackageError
from code_versions.service import CodeBuildError, build_trusted_version


class Command(BaseCommand):
    help = "Verify the expected Git HEAD and sync its Mini Program source to private storage."

    def add_arguments(self, parser):
        parser.add_argument("--expected-revision", required=True)

    def handle(self, *args, **options):
        try:
            version = build_trusted_version(settings.BASE_DIR.parent,
                                            options["expected_revision"])
        except (PackageError, CodeBuildError) as exc:
            raise CommandError(f"Mini Program deployment sync failed: {exc.code}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Committed Mini Program source synchronized: {version.version_label} "
            f"({version.package_sha256})"))
