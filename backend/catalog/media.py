"""Local persistent product media storage and conservative file validation."""

import hashlib
import os
import re
import shutil
import struct
import subprocess
import uuid
import zlib

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone

from accounts.security import audit, permissions, require_live

from .models import Asset, ProductGalleryImage
from .storage import LocalStorage, lock_storage, recover_media_storage, storage_unavailable
from .validation import CatalogError, list_field, uuid_field


MAX_DIMENSION = 8192
MAX_PIXELS = 32_000_000
RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def dimensions(width, height):
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION or width * height > MAX_PIXELS:
        raise CatalogError("图片尺寸超出支持范围。", "MEDIA_INVALID")
    return width, height


def png_dimensions(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CatalogError("仅支持 PNG 或 JPEG 图片。", "MEDIA_INVALID")
    offset = 8
    found_header = False
    found_pixels = False
    width = height = 0
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + size
        if size > len(data) or end > len(data):
            break
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", data, offset + 8 + size)[0]
        if zlib.crc32(kind + payload) != crc:
            break
        if kind in (b"acTL", b"fcTL", b"fdAT"):
            raise CatalogError("兜底 PNG 须为静态图片，不支持 APNG。", "MEDIA_INVALID")
        if not found_header:
            if kind != b"IHDR" or size != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if payload[9] not in (0, 2, 3, 4, 6) or payload[10:] != b"\x00\x00\x00":
                break
            found_header = True
        if kind == b"IDAT":
            found_pixels = True
        if kind == b"IEND":
            if size == 0 and found_header and found_pixels and end == len(data):
                return dimensions(width, height)
            break
        offset = end
    raise CatalogError("PNG 文件结构或校验值无效。", "MEDIA_INVALID")


def jpeg_dimensions(data):
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise CatalogError("JPEG 文件结构无效。", "MEDIA_INVALID")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA:
            break
        if offset + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 7:
                break
            height, width = struct.unpack_from(">HH", data, offset + 3)
            return dimensions(width, height)
        offset += length
    raise CatalogError("JPEG 图片缺少有效尺寸信息。", "MEDIA_INVALID")


def gif_dimensions(data):
    if len(data) < 14 or data[:6] not in (b"GIF87a", b"GIF89a") or data[-1:] != b";":
        raise CatalogError("GIF 文件结构无效。", "MEDIA_INVALID")
    width, height = struct.unpack_from("<HH", data, 6)
    return dimensions(width, height)


def inspect_mp4(path):
    offset = 0
    boxes = set()
    file_size = path.stat().st_size
    with path.open("rb") as source:
        while offset + 8 <= file_size:
            source.seek(offset)
            header_bytes = source.read(16)
            size = struct.unpack_from(">I", header_bytes)[0]
            kind = header_bytes[4:8]
            header = 8
            if size == 1:
                if len(header_bytes) < 16:
                    break
                size = struct.unpack_from(">Q", header_bytes, 8)[0]
                header = 16
            elif size == 0:
                size = file_size - offset
            if size < header or offset + size > file_size:
                break
            if offset == 0 and (kind != b"ftyp" or size < 16):
                break
            boxes.add(kind)
            offset += size
    if offset != file_size or not {b"ftyp", b"moov", b"mdat"}.issubset(boxes):
        raise CatalogError("MP4 文件结构无效或缺少视频数据。", "MEDIA_INVALID")


def inspect_file(kind, path):
    if kind == Asset.Kind.VIDEO:
        inspect_mp4(path)
        return "video/mp4", "mp4", None, None
    data = path.read_bytes()
    if kind == Asset.Kind.GIF:
        width, height = gif_dimensions(data)
        return "image/gif", "gif", width, height
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = png_dimensions(data)
        return "image/png", "png", width, height
    if data.startswith(b"\xff\xd8"):
        width, height = jpeg_dimensions(data)
        return "image/jpeg", "jpg", width, height
    raise CatalogError("仅支持 PNG 或 JPEG 图片。", "MEDIA_INVALID")


def verify_decodable(path, kind):
    binary = shutil.which(settings.PRODUCT_MEDIA_FFMPEG_BIN)
    if not binary:
        raise CatalogError("服务端尚未配置媒体解码器。", "MEDIA_VALIDATOR_UNAVAILABLE", 503)
    args = [binary, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-threads", "2",
            "-i", str(path), "-map", "0:v:0", "-f", "null", "-", "-progress", "pipe:1"]
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15 if kind == Asset.Kind.IMAGE else 45, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CatalogError("素材解码超时，请使用更短或更小的文件。", "MEDIA_INVALID") from exc
    frames = [int(line[6:]) for line in result.stdout.splitlines()
              if line.startswith(b"frame=") and line[6:].isdigit()]
    minimum_frames = 2 if kind == Asset.Kind.GIF else 1
    if result.returncode or not frames or frames[-1] < minimum_frames:
        raise CatalogError("素材无法解码或不含有效图像帧。", "MEDIA_INVALID")


def orphan_assets():
    from .models import Product
    from pages.asset_references import retained_asset_id_queries
    unbound = Asset.objects.exclude(id__in=Product.objects.filter(main_image__isnull=False).values("main_image_id"))\
        .exclude(id__in=Product.objects.filter(video__isnull=False).values("video_id"))\
        .exclude(id__in=ProductGalleryImage.objects.values("asset_id"))
    for query in retained_asset_id_queries():
        unbound = unbound.exclude(id__in=query)
    return unbound


def purge_expired_orphans():
    recover_media_storage()
    cutoff = timezone.now() - settings.PRODUCT_MEDIA_ORPHAN_TTL
    removed = 0
    storage = LocalStorage()
    try:
        for identifier in orphan_assets().filter(created_at__lt=cutoff).values_list("id", flat=True):
            with transaction.atomic():
                lock_storage()
                asset = Asset.objects.select_for_update().filter(id=identifier).first()
                if not asset or not orphan_assets().filter(id=identifier, created_at__lt=cutoff).exists():
                    continue
                marker = storage.stage_intent(asset.id, asset.stored_name)
                asset.delete()
                transaction.on_commit(lambda key=marker: storage.finish_intent(key))
                removed += 1
    except OSError as exc:
        raise storage_unavailable(exc) from exc
    return removed


def revalidate_asset_actor(request, permission, actor=None):
    """Recheck authority after every blocking lock, including trusted services."""
    if not hasattr(request, "user") and actor is not None:
        from accounts.models import AdminAccount
        fresh = AdminAccount.objects.filter(pk=actor.pk, enabled=True,
                                            auth_version=actor.auth_version).first()
        if fresh is None:
            raise CatalogError("请重新登录。", "SESSION_EXPIRED", 401)
        if permission not in permissions(fresh):
            raise CatalogError("当前账号没有此操作权限。", "PERMISSION_DENIED", 403)
        return fresh
    actor, bad = require_live(request, permission)
    if bad is not None:
        if bad.status_code == 401:
            raise CatalogError("请重新登录。", "SESSION_EXPIRED", 401)
        raise CatalogError("当前账号没有此操作权限。", "PERMISSION_DENIED", 403)
    return actor


def delete_unbound_asset(request, asset_id, actor):
    storage = LocalStorage()
    try:
        with transaction.atomic():
            lock_storage()
            asset = Asset.objects.select_for_update().filter(pk=asset_id).first()
            actor = revalidate_asset_actor(request, "asset.delete", actor)
            if not asset:
                raise CatalogError("素材不存在。", "NOT_FOUND", 404)
            if not orphan_assets().filter(pk=asset.pk).exists():
                raise CatalogError("素材仍被引用，不能删除。", "MEDIA_REFERENCED", 409)
            marker = storage.stage_intent(asset.id, asset.stored_name)
            audit(request, "asset.delete", "asset", asset.id, actor,
                  before={"kind": asset.kind, "byteSize": asset.byte_size, "sha256": asset.sha256})
            asset.delete()
            transaction.on_commit(lambda: storage.finish_intent(marker))
    except OSError as exc:
        raise storage_unavailable(exc) from exc


def available_asset(asset):
    """Binding and publication reject missing files and expired orphan leases."""
    if asset.created_at < timezone.now() - settings.PRODUCT_MEDIA_ORPHAN_TTL and \
            orphan_assets().filter(pk=asset.pk).exists():
        return False
    try:
        return asset_path(asset).is_file()
    except CatalogError:
        return False
    except OSError as exc:
        raise storage_unavailable(exc) from exc


def lock_available_assets(identifiers):
    found = {asset.id: asset for asset in Asset.objects.select_for_update().filter(
        id__in=identifiers).order_by("id")}
    if len(found) != len(set(identifiers)) or any(not available_asset(asset) for asset in found.values()):
        raise CatalogError("素材不存在、已过期或文件丢失，请重新上传。", "MEDIA_INVALID")
    return found


def check_orphan_quota(actor, incoming_bytes):
    unbound = orphan_assets()
    personal = unbound.filter(created_by=actor).aggregate(total=Sum("byte_size"))["total"] or 0
    global_total = unbound.aggregate(total=Sum("byte_size"))["total"] or 0
    if personal + incoming_bytes > settings.PRODUCT_MEDIA_ORPHAN_USER_QUOTA_BYTES or \
            global_total + incoming_bytes > settings.PRODUCT_MEDIA_ORPHAN_GLOBAL_QUOTA_BYTES:
        raise CatalogError("未绑定素材已达空间上限，请清理或绑定已有素材。", "MEDIA_QUOTA", 409)


def asset_path(asset):
    if not isinstance(asset.stored_name, str) or asset.stored_name.split("/")[0] not in {
            "media", "product", "startup", "page"}:
        raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
    return LocalStorage().path(asset.stored_name)


def store_asset(request, uploaded, kind, actor):
    if kind not in Asset.Kind.values:
        raise CatalogError("素材类型不正确。", "MEDIA_INVALID")
    maximum = {Asset.Kind.IMAGE: settings.PRODUCT_IMAGE_MAX_BYTES,
               Asset.Kind.VIDEO: settings.PRODUCT_VIDEO_MAX_BYTES,
               Asset.Kind.GIF: settings.STARTUP_GIF_MAX_BYTES}[kind]
    if not 0 < uploaded.size <= maximum:
        raise CatalogError("素材文件为空或超过大小限制。", "MEDIA_INVALID")
    purge_expired_orphans()
    # A shop-wide PostgreSQL transaction lock makes the quota check and insert
    # one operation even when several accounts upload at the same time.
    asset = None
    try:
        with transaction.atomic():
            lock_storage()
            actor = revalidate_asset_actor(request, "asset.upload", actor)
            check_orphan_quota(actor, uploaded.size)
            asset = _store_asset_checked(uploaded, kind, actor, maximum)
            audit(request, "asset.upload", "asset", asset.id, actor,
                  after={"kind": asset.kind, "byteSize": asset.byte_size, "sha256": asset.sha256})
        return asset
    except Exception as exc:
        if asset is not None:
            # A failed DB/audit operation must never leave a usable asset row.
            # If the disk also fails, its durable intent remains recoverable.
            try:
                LocalStorage().discard(asset.stored_name)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise storage_unavailable(exc) from exc
        raise


def _store_asset_checked(uploaded, kind, actor, maximum):
    storage = LocalStorage()
    temporary = storage.temporary()
    total = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("xb") as destination:
            os.chmod(temporary, 0o600)
            for chunk in uploaded.chunks():
                total += len(chunk)
                if total > maximum:
                    raise CatalogError("素材文件超过大小限制。", "MEDIA_INVALID")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if total == 0 or total != uploaded.size:
            raise CatalogError("素材上传不完整，请重新上传。", "MEDIA_INVALID")
        content_type, extension, width, height = inspect_file(kind, temporary)
        verify_decodable(temporary, kind)
        if uploaded.content_type != content_type:
            raise CatalogError("文件声明类型与实际内容不一致。", "MEDIA_INVALID")
        identifier = uuid.uuid4()
        area = "startup" if kind == Asset.Kind.GIF else "product"
        stored_name = storage.key("media", f"{area}/{identifier.hex[:2]}/{identifier}.{extension}")
        marker = storage.stage_intent(identifier, stored_name)
        storage.promote(temporary, stored_name)
        try:
            asset = Asset.objects.create(id=identifier, kind=kind, content_type=content_type,
                byte_size=total, width=width, height=height, sha256=digest.hexdigest(),
                original_name=uploaded.name.replace("\\", "/").split("/")[-1][:120],
                stored_name=stored_name, created_by=actor)
            transaction.on_commit(lambda: storage.finish_intent(marker))
            return asset
        except Exception:
            try:
                storage.discard(stored_name)
            except OSError:
                pass
            raise
    finally:
        # Keep an inaccessible failed temporary for TTL recovery if disk cleanup fails.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_media(values, product=None):
    main_id = values.get("mainImageAssetId", str(product.main_image_id) if product and product.main_image_id else None)
    video_id = values.get("videoAssetId", str(product.video_id) if product and product.video_id else None)
    gallery_ids = values.get("galleryAssetIds", [str(row.asset_id) for row in
        product.gallery_images.order_by("position")] if product else [])
    gallery_ids = list_field(gallery_ids, "商品附图", 8)
    identifiers = [uuid_field(value, "素材 ID") for value in [main_id, video_id, *gallery_ids] if value is not None]
    if len(identifiers) != len(set(identifiers)):
        raise CatalogError("同一素材不能重复绑定。", "MEDIA_INVALID")
    found = lock_available_assets(identifiers)
    main = found.get(uuid_field(main_id, "主图 ID")) if main_id is not None else None
    video = found.get(uuid_field(video_id, "视频 ID")) if video_id is not None else None
    gallery = [found[uuid_field(value, "附图 ID")] for value in gallery_ids]
    if main and (main.kind != Asset.Kind.IMAGE or main.width != main.height):
        raise CatalogError("商品主图必须是正方形图片。", "MEDIA_INVALID")
    if video and video.kind != Asset.Kind.VIDEO:
        raise CatalogError("商品视频须为 MP4。", "MEDIA_INVALID")
    if any(asset.kind != Asset.Kind.IMAGE for asset in gallery):
        raise CatalogError("商品附图须为图片。", "MEDIA_INVALID")
    attached = ([main] if main else []) + gallery + ([video] if video else [])
    if any(not available_asset(asset) for asset in attached):
        raise CatalogError("素材文件丢失，请重新上传。", "MEDIA_INVALID")
    return main, gallery, video


def set_gallery(product, gallery):
    ProductGalleryImage.objects.filter(product=product).delete()
    ProductGalleryImage.objects.bulk_create([
        ProductGalleryImage(product=product, asset=asset, position=index)
        for index, asset in enumerate(gallery)
    ])


class RangeContent:
    """Open and pre-read before committing HTTP headers; always close on cancel."""
    def __init__(self, path, start, length):
        self.source = path.open("rb")
        self.length = length
        try:
            self.source.seek(start)
            expected = min(65536, length)
            self.first = self.source.read(expected)
            if len(self.first) != expected:
                raise OSError("Media changed before range delivery")
        except Exception:
            self.close()
            raise

    def close(self):
        self.source.close()

    def __iter__(self):
        try:
            yield self.first
            remaining = self.length - len(self.first)
            while remaining:
                chunk = self.source.read(min(65536, remaining))
                if not chunk:
                    raise OSError("Media truncated during range delivery")
                remaining -= len(chunk)
                yield chunk
        finally:
            self.close()


def serve_asset(request, asset, public=False):
    try:
        return _serve_asset(request, asset, public=public)
    except OSError as exc:
        raise storage_unavailable(exc) from exc


def _serve_asset(request, asset, public=False):
    path = asset_path(asset)
    if not path.is_file():
        return HttpResponse(status=404)
    size = path.stat().st_size
    requested_range = request.headers.get("Range") if asset.kind == Asset.Kind.VIDEO else None
    if requested_range:
        match = RANGE_PATTERN.fullmatch(requested_range)
        if not match or not any(match.groups()):
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        start_raw, end_raw = match.groups()
        if start_raw:
            start = int(start_raw)
            end = min(int(end_raw), size - 1) if end_raw else size - 1
        else:
            suffix = int(end_raw)
            start, end = max(size - suffix, 0), size - 1
        if start >= size or end < start:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        response = StreamingHttpResponse(RangeContent(path, start, end - start + 1),
                                         status=206, content_type=asset.content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    else:
        response = FileResponse(path.open("rb"), content_type=asset.content_type)
        response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes" if asset.kind == Asset.Kind.VIDEO else "none"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    response["Cache-Control"] = "no-store" if public else "private, no-store"
    return response
