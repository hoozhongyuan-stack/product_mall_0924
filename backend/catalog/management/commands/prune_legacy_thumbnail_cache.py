"""Remove only validated v1 derivatives after E5 has stopped every writer."""
import os
import re

from django.core.management.base import BaseCommand, CommandError

from catalog.storage import LocalStorage
from catalog.validation import CatalogError

UUID_DIRECTORY = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ASSET_FILE = re.compile(r"(?:[0-9a-f]{64}\.jpg|asset\.lock|tmp[a-z0-9_]{8}\.part)")
MAX_NODES = 100000


class Command(BaseCommand):
    help = "Prune validated legacy .thumbnails/v1 only while E5_WRITERS_PAUSED=1."

    def handle(self, *args, **options):
        if os.environ.get("E5_WRITERS_PAUSED") != "1":
            raise CommandError("E5 writers must be paused before pruning legacy thumbnails.")
        storage = LocalStorage()
        try:
            root = storage.path(".thumbnails/v1/placeholder").parent
            if not root.exists():
                self.stdout.write("removedFiles=0 removedDirectories=0")
                return
            if not root.is_dir():
                raise CommandError("Unsafe legacy thumbnail cache layout; pruning refused.")
            files, directories = self.validated_nodes(root)
            # Validate the whole tree first: any unknown/special node leaves
            # every derivative intact. E5 owns the stop/prune/backup sequence.
            for path in files:
                key = str(path.relative_to(storage.root))
                storage.path(key).unlink()
            for path in reversed(directories):
                key = str(path.relative_to(storage.root))
                storage.path(key).rmdir()
            storage.sync_directory(root.parent)
        except (OSError, CatalogError):
            raise CommandError("Legacy thumbnail cache cleanup failed; keep E5 writers paused.") from None
        self.stdout.write(f"removedFiles={len(files)} removedDirectories={len(directories)}")

    def validated_nodes(self, root):
        files, directories = [], [root]
        for directory in root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not (
                    directory.name == "slots" or UUID_DIRECTORY.fullmatch(directory.name)):
                raise CommandError("Unsafe legacy thumbnail cache layout; pruning refused.")
            directories.append(directory)
            if len(files) + len(directories) > MAX_NODES:
                raise CommandError("Legacy thumbnail cache exceeds the cleanup node limit; pruning refused.")
            for path in directory.iterdir():
                allowed = path.name in {"0.lock", "1.lock"} if directory.name == "slots" else ASSET_FILE.fullmatch(path.name)
                if path.is_symlink() or not path.is_file() or not allowed:
                    raise CommandError("Unsafe legacy thumbnail cache layout; pruning refused.")
                files.append(path)
                if len(files) + len(directories) > MAX_NODES:
                    raise CommandError("Legacy thumbnail cache exceeds the cleanup node limit; pruning refused.")
        return files, directories
