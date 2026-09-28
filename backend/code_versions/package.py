"""Deterministic source snapshot from a deployment-owned Mini Program tree."""

import hashlib
import io
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT_FILES = ("app.js", "app.json", "app.wxss", "project.config.json", "sitemap.json")
SOURCE_DIRS = ("assets", "components", "lib", "pages")
IGNORED_DIRS = frozenset({"node_modules", "tests", "__tests__", "__pycache__"})
EXTENSIONS = frozenset({".js", ".json", ".wxml", ".wxss", ".svg", ".png",
                        ".jpg", ".jpeg", ".gif", ".webp"})


class PackageError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourcePackage:
    data: bytes
    source_digest: str
    package_sha256: str
    file_count: int


def _safe_read(path, root, maximum_bytes):
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PackageError("SOURCE_INVALID") from exc
    parts = path.absolute().parts
    if ".." in parts or len(parts) < 2:
        raise PackageError("SOURCE_INVALID")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        parent = os.open("/", directory_flags)
        try:
            for part in parts[1:-1]:
                next_parent = os.open(part, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = next_parent
            descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        finally:
            os.close(parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise PackageError("SOURCE_INVALID")
            if before.st_size > maximum_bytes:
                raise PackageError("PACKAGE_TOO_LARGE")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(maximum_bytes + 1)
            after = os.fstat(descriptor)
            if len(data) > maximum_bytes:
                raise PackageError("PACKAGE_TOO_LARGE")
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise PackageError("SOURCE_CHANGED")
            return data
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as exc:
        raise PackageError("SOURCE_INVALID") from exc


def _source_paths(root):
    if not root.is_dir() or root.is_symlink():
        raise PackageError("SOURCE_UNAVAILABLE")
    if not (root / "app.js").is_file() or not (root / "app.json").is_file():
        raise PackageError("SOURCE_INVALID")
    paths = [root / name for name in ROOT_FILES if (root / name).exists() or (root / name).is_symlink()]
    for name in SOURCE_DIRS:
        directory = root / name
        if directory.is_symlink():
            raise PackageError("SOURCE_INVALID")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise PackageError("SOURCE_INVALID")
        def walk_error(exc):
            raise PackageError("SOURCE_UNAVAILABLE") from exc

        for current, dirs, files in os.walk(directory, followlinks=False, onerror=walk_error):
            dirs[:] = [part for part in sorted(dirs) if part not in IGNORED_DIRS and not part.startswith(".")]
            for part in dirs:
                if (Path(current) / part).is_symlink():
                    raise PackageError("SOURCE_INVALID")
            for filename in sorted(files):
                if filename.startswith(".") or filename in {"project.private.config.json", "package-lock.json"}:
                    continue
                entry = Path(current) / filename
                if entry.is_symlink() or entry.suffix.lower() not in EXTENSIONS:
                    raise PackageError("SOURCE_INVALID")
                paths.append(entry)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def build_package(root, maximum_bytes):
    root = Path(root).absolute()
    if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise PackageError("PACKAGE_TOO_LARGE")
    digest = hashlib.sha256()
    data = io.BytesIO()
    source_bytes = 0
    paths = _source_paths(root)
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
                         allowZip64=True) as archive:
        for path in paths:
            name = path.relative_to(root).as_posix()
            content = _safe_read(path, root, maximum_bytes - source_bytes)
            source_bytes += len(content)
            if source_bytes > maximum_bytes:
                raise PackageError("PACKAGE_TOO_LARGE")
            encoded = name.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            if data.tell() > maximum_bytes:
                raise PackageError("PACKAGE_TOO_LARGE")
    package = data.getvalue()
    if len(package) > maximum_bytes:
        raise PackageError("PACKAGE_TOO_LARGE")
    # The deployment source must remain stable throughout the full read. A
    # second complete pass detects ordinary concurrent checkout/release edits.
    if paths != _source_paths(root):
        raise PackageError("SOURCE_CHANGED")
    verify = hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix().encode("utf-8")
        try:
            content = _safe_read(path, root, maximum_bytes)
        except PackageError as exc:
            raise PackageError("SOURCE_CHANGED") from exc
        verify.update(len(name).to_bytes(4, "big"))
        verify.update(name)
        verify.update(len(content).to_bytes(8, "big"))
        verify.update(content)
    if verify.hexdigest() != digest.hexdigest():
        raise PackageError("SOURCE_CHANGED")
    return SourcePackage(package, digest.hexdigest(), hashlib.sha256(package).hexdigest(), len(paths))
