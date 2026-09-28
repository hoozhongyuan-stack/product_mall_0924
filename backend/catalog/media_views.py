"""Authenticated uploads and permission checked media delivery."""

from django.conf import settings
from django.db.models import Q
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie

from common.http import offset_response, method
from accounts.security import audit, error, permissions, require, response

from .media import orphan_assets, serve_asset, store_asset
from .models import Asset, Category, Product, Sku
from .presentation import asset_data
from .validation import CatalogError
from pages.media_access import (asset_is_available_to_page_reader,
                                asset_is_in_current_visible_publication,
                                storefront_readable_domains,
                                asset_is_in_current_storefront_publication)
from pages.startup_media_access import (asset_is_available_to_startup_reader,
                                        asset_is_in_current_startup_publication)


@never_cache
@vary_on_cookie
def assets_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "asset.upload" if request.method == "POST" else None)
    if bad:
        return bad
    try:
        if request.method == "GET":
            from .asset_library import library_page
            granted = set(permissions(actor))
            if not granted.intersection({"asset.read", "asset.upload"}):
                return error(request, 403, "PERMISSION_DENIED", "当前账号没有素材查询权限。")
            return offset_response(request, library_page(request, actor, granted))
        # Reject declared oversize requests before Django parses multipart data.
        maximum = max(settings.PRODUCT_VIDEO_MAX_BYTES, settings.STARTUP_GIF_MAX_BYTES,
                      settings.PRODUCT_IMAGE_MAX_BYTES) + 65536
        if int(request.META.get("CONTENT_LENGTH") or 0) > maximum:
            raise CatalogError("素材文件超过大小限制。", "MEDIA_INVALID")
        if request.content_type != "multipart/form-data":
            raise CatalogError("请上传 multipart/form-data 文件。", "MEDIA_INVALID")
        if set(request.POST) != {"kind"} or set(request.FILES) != {"file"}:
            raise CatalogError("请提供素材类型和单个文件。", "MEDIA_INVALID")
        asset = store_asset(request, request.FILES["file"], request.POST["kind"], actor)
        return response(request, asset_data(asset), 201)
    except (CatalogError, ValueError) as exc:
        if isinstance(exc, CatalogError):
            return error(request, exc.status, exc.code, str(exc))
        return error(request, 400, "MEDIA_INVALID", "素材文件大小无效。")


@never_cache
@vary_on_cookie
def asset_references_view(request, asset_id):
    bad = method(request, "GET")
    if bad:
        return bad
    actor, bad = require(request, "asset.read")
    if bad:
        return bad
    if not Asset.objects.filter(id=asset_id).exists():
        return error(request, 404, "NOT_FOUND", "素材不存在。")
    try:
        from .asset_library import references_page
        return offset_response(request, references_page(request, asset_id, set(permissions(actor))))
    except CatalogError as exc:
        return error(request, exc.status, exc.code, str(exc))


@never_cache
@vary_on_cookie
def asset_delete_view(request, asset_id):
    bad = method(request, "DELETE")
    if bad:
        return bad
    actor, bad = require(request, "asset.delete")
    if bad:
        return bad
    try:
        from .media import delete_unbound_asset
        delete_unbound_asset(request, asset_id, actor)
        return response(request, {"deleted": True})
    except CatalogError as exc:
        return error(request, exc.status, exc.code, str(exc))


def file_response(request, asset, *, public=False):
    try:
        return serve_asset(request, asset, public=public)
    except CatalogError as exc:
        return error(request, exc.status, exc.code, str(exc))
    except OSError:
        return error(request, 503, "MEDIA_STORAGE_UNAVAILABLE", "素材存储暂不可用，请稍后重试。")


def admin_asset_file_view(request, asset_id):
    bad = method(request, "GET")
    if bad:
        return bad
    actor, bad = require(request)
    if bad:
        return bad
    granted = set(permissions(actor))
    if "asset.read" not in granted:
        page_reference = "page.read" in granted and asset_is_available_to_page_reader(asset_id)
        startup_reference = "startup.read" in granted and asset_is_available_to_startup_reader(asset_id)
        storefront_reference = any(f"{domain}.read" in granted for domain in
                                   storefront_readable_domains(asset_id))
        own_unbound_upload = "asset.upload" in granted and (
            orphan_assets().filter(id=asset_id, created_by=actor).exists()
        )
        if not page_reference and not startup_reference and not storefront_reference and not own_unbound_upload:
            audit(request, "permission.denied", "asset", asset_id, actor, result="DENIED")
            return error(request, 403, "PERMISSION_DENIED", "当前账号没有此素材读取权限。")
    asset = Asset.objects.filter(id=asset_id).first()
    return file_response(request, asset) if asset else error(request, 404, "NOT_FOUND", "素材不存在。")


def public_asset_file_view(request, asset_id):
    bad = method(request, "GET")
    if bad:
        return bad
    product = Product.objects.filter(
        status=Product.Status.ON_SALE,
        main_image__isnull=False,
        category__status=Category.Status.ACTIVE,
        category__parent__status=Category.Status.ACTIVE,
        skus__sale_status=Sku.SaleStatus.ON_SALE,
    ).filter(Q(main_image_id=asset_id) | Q(video_id=asset_id) |
             Q(gallery_images__asset_id=asset_id)).exists()
    published_page = asset_is_in_current_visible_publication(asset_id)
    published_startup = asset_is_in_current_startup_publication(asset_id)
    published_storefront = asset_is_in_current_storefront_publication(asset_id)
    from points_exchange.media_access import asset_visible_for_exchange
    exchange=asset_visible_for_exchange(asset_id)
    if not product and not published_page and not published_startup and not published_storefront and not exchange:
        return error(request, 404, "NOT_FOUND", "素材不可用。")
    asset = Asset.objects.filter(id=asset_id).first()
    return file_response(request, asset, public=True) if asset else error(request, 404, "NOT_FOUND", "素材不可用。")
