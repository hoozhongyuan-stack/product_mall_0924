"""Warm a bounded set of media derivatives without modifying business facts."""
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Asset
from catalog.thumbnails import thumbnail_path
from catalog.validation import CatalogError


class Command(BaseCommand):
    help = "Warm static thumbnails for newest image/GIF assets (default 50, maximum 200)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        limit = options["limit"]
        if type(limit) is not int or not 1 <= limit <= 200:
            raise CommandError("limit must be between 1 and 200")
        generated = failures = total_bytes = 0
        assets = Asset.objects.filter(kind__in=(Asset.Kind.IMAGE, Asset.Kind.GIF)).order_by("-created_at", "-id")[:limit]
        for asset in assets:
            try:
                path = thumbnail_path(asset)
                total_bytes += path.stat().st_size
                generated += 1
            except (CatalogError, OSError) as exc:
                failures += 1
                code = exc.code if isinstance(exc, CatalogError) else "MEDIA_STORAGE_UNAVAILABLE"
                self.stderr.write(f"asset={asset.id} error={code}")
        self.stdout.write(f"generated={generated} failed={failures} bytes={total_bytes}")
        if failures:
            raise CommandError("Some thumbnails are unavailable; original media and business records are unchanged.")
