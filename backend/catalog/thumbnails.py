"""Regenerable private static derivatives; no asset or original file mutations."""
import base64
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.http import FileResponse

from .media import asset_path, dimensions, gif_dimensions, jpeg_dimensions, png_dimensions
from .models import Asset
from .storage import LocalStorage
from .validation import CatalogError

EDGE = 320
MAX_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 10 * 1024 * 1024
VERSION = "v2"


def unavailable():
    return CatalogError("缩略图暂不可用，请稍后重试。", "MEDIA_THUMBNAIL_UNAVAILABLE", 503)


@contextmanager
def file_lock(path, *, wait=9):
    """flock coordinates both threads and workers without database writes."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise unavailable()
                time.sleep(0.025)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def generation_slot(storage):
    """At most two decoder processes across application workers per volume."""
    descriptor = None
    try:
        for number in range(2):
            path = storage.prepare_parent(f".thumbnails/{VERSION}/slots/{number}.lock")
            candidate = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                descriptor = candidate
                break
            except BlockingIOError:
                os.close(candidate)
        if descriptor is None:
            raise unavailable()
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)


def source_state(asset):
    if asset.kind not in (Asset.Kind.IMAGE, Asset.Kind.GIF):
        raise CatalogError("此素材不支持静态缩略图。", "MEDIA_INVALID")
    if not 0 < asset.byte_size <= MAX_SOURCE_BYTES:
        raise CatalogError("缩略图源文件大小超出支持范围。", "MEDIA_INVALID")
    dimensions(asset.width or 0, asset.height or 0)
    source = asset_path(asset)
    if not source.is_file():
        raise CatalogError("素材文件不存在。", "NOT_FOUND", 404)
    stat = source.stat()
    if stat.st_size != asset.byte_size:
        raise CatalogError("素材源文件校验不一致。", "MEDIA_INVALID")
    fingerprint = f"{asset.sha256}:{asset.kind}:{asset.width}:{asset.height}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_ino}"
    return source, hashlib.sha256(fingerprint.encode()).hexdigest()


def source_bytes(asset, source):
    # Validate bounded bytes before passing a pipe or private seekable copy to
    # the fixed decoder; it never reopens a potentially swapped asset pathname.
    with source.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) != asset.byte_size or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise CatalogError("素材源文件校验不一致。", "MEDIA_INVALID")
    if asset.kind == Asset.Kind.GIF:
        actual = gif_dimensions(data)
        image_format = "gif"
    elif asset.content_type == "image/png":
        actual = png_dimensions(data)
        image_format = "png_pipe"
    elif asset.content_type == "image/jpeg":
        actual = jpeg_dimensions(data)
        image_format = "jpeg_pipe"
    else:
        raise CatalogError("缩略图源格式不支持。", "MEDIA_INVALID")
    if actual != (asset.width, asset.height):
        raise CatalogError("素材源尺寸校验不一致。", "MEDIA_INVALID")
    return data, image_format


def valid_thumbnail(path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        return False
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return False
        width, height = jpeg_dimensions(data)
        return max(width, height) <= EDGE
    except CatalogError:
        return False


@contextmanager
def decoder_input(data, image_format):
    if image_format != "gif":
        yield "pipe", "pipe:0", {"input": data}
        return
    # GIF demuxers can seek while probing/replaying multi-frame input. Use an
    # unlinked private copy of the verified bytes, never the original pathname.
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.flush()
        source.seek(0)
        descriptor = source.fileno()
        prefix = "/proc/self/fd" if sys.platform.startswith("linux") else "/dev/fd"
        yield "file", f"{prefix}/{descriptor}", {"pass_fds": (descriptor,)}


def generate_thumbnail(asset, source, destination):
    binary = shutil.which(settings.PRODUCT_MEDIA_FFMPEG_BIN)
    if not binary:
        raise unavailable()
    data, image_format = source_bytes(asset, source)
    # Decode one frame, with bounded dimensions/allocation/time and no network
    # protocols; the existing deployment image already contains ffmpeg.
    arguments = [binary, "-hide_banner", "-nostdin", "-v", "error", "-xerror",
                 "-max_alloc", "67108864", "-threads", "1", "-filter_threads", "1",
                 "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn",
                 "-vf", f"scale=w='min({EDGE},iw)':h='min({EDGE},ih)':force_original_aspect_ratio=decrease",
                 "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-threads", "1", "-q:v", "8", "-fs", str(MAX_BYTES + 1),
                 "-f", "image2", "-y", str(destination)]
    try:
        with decoder_input(data, image_format) as (protocol, input_path, input_options):
            input_arguments = ["-protocol_whitelist", protocol, "-f", image_format, "-i", input_path]
            # Input options must precede output options (map/frame/scale/etc.).
            position = arguments.index("-map")
            invocation = [*arguments[:position], *input_arguments, *arguments[position:]]
            result = subprocess.run(invocation, **input_options, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=8, check=False)
    except subprocess.TimeoutExpired as exc:
        raise unavailable() from exc
    if result.returncode or result.stderr.strip() or not valid_thumbnail(destination):
        raise CatalogError("素材无法生成静态缩略图。", "MEDIA_INVALID")


def thumbnail_path(asset):
    storage = LocalStorage()
    source, fingerprint = source_state(asset)
    directory_key = f".thumbnails/{VERSION}/{uuid.UUID(str(asset.id)).hex}"
    # Preserve all 256 hash bits while staying below E5's 98-byte path bound.
    filename = base64.urlsafe_b64encode(bytes.fromhex(fingerprint)).decode("ascii").rstrip("=")
    target_key = f"{directory_key}/{filename}.jpg"
    target = storage.prepare_parent(target_key)
    lock = storage.prepare_parent(f"{directory_key}/asset.lock")
    with file_lock(lock):
        # Recheck source after any blocking lock, including cache hits.
        current_source, current_fingerprint = source_state(asset)
        if current_fingerprint != fingerprint:
            raise unavailable()
        if valid_thumbnail(target):
            return target
        with generation_slot(storage):
            descriptor, name = tempfile.mkstemp(suffix=".part", dir=target.parent)
            os.close(descriptor)
            temporary = target.parent / os.path.basename(name)
            try:
                generate_thumbnail(asset, current_source, temporary)
                if source_state(asset)[1] != fingerprint:
                    raise unavailable()
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(temporary, storage.path(target_key))
                storage.sync_directory(target.parent)
                # Keep only this source version under the asset's lock.
                for obsolete in target.parent.glob("*.jpg"):
                    if obsolete != target:
                        storage.path(f"{directory_key}/{obsolete.name}").unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)
        return target


def discard_asset_thumbnails(asset_id, *, storage=None):
    """Called only after original deletion; never unlink active lock inodes."""
    storage = storage or LocalStorage()
    identifier = uuid.UUID(str(asset_id))
    for key in (f".thumbnails/{VERSION}/{identifier.hex}", f".thumbnails/v1/{identifier}"):
        directory = storage.path(f"{key}/asset.lock").parent
        if not directory.is_dir():
            continue
        lock = storage.path(f"{key}/asset.lock")
        with file_lock(lock):
            for pattern in ("*.jpg", "*.part"):
                for derivative in directory.glob(pattern):
                    storage.path(f"{key}/{derivative.name}").unlink(missing_ok=True)
            storage.sync_directory(directory)


def serve_thumbnail(request, asset, *, public=False, reauthorize=None):
    path = thumbnail_path(asset)
    if reauthorize is not None:
        bad = reauthorize()
        if bad is not None:
            return bad
    source_state(asset)
    result = FileResponse(path.open("rb"), content_type="image/jpeg")
    result["Content-Disposition"] = "inline"
    result["X-Content-Type-Options"] = "nosniff"
    result["Accept-Ranges"] = "none"
    result["Cache-Control"] = "no-store" if public else "private, no-store"
    return result
