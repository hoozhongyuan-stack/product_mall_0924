"""Bounded material metadata and retained business-reference read model."""
from django.conf import settings
from django.db.models import F, Subquery
from django.utils import timezone

from pages.asset_references import reference_queries, reference_query

from .media import asset_path, orphan_assets
from .models import Asset, Product, ProductGalleryImage
from .presentation import asset_data
from .validation import CatalogError


def pagination(request):
    if any(len(request.GET.getlist(key)) != 1 for key in request.GET):
        raise CatalogError("查询参数不能重复。")
    try:
        page, size = int(request.GET.get("page", "1")), int(request.GET.get("pageSize", "20"))
    except ValueError:
        raise CatalogError("分页参数不正确。") from None
    if not 1 <= page <= 100000 or not 1 <= size <= 100:
        raise CatalogError("分页参数不正确。")
    return page, size


def library_page(request, actor, granted):
    if set(request.GET) - {"page", "pageSize", "kind", "q", "binding", "square"}:
        raise CatalogError("素材查询含有不支持的参数。")
    page, size = pagination(request)
    query = Asset.objects.all()
    unbound = orphan_assets()
    if "asset.read" not in granted:
        query = query.filter(created_by=actor, id__in=Subquery(unbound.values("id")))
    kind, binding, square = (request.GET.get(key, "") for key in ("kind", "binding", "square"))
    search = request.GET.get("q", "").strip()
    if kind and kind not in Asset.Kind.values or binding not in ("", "BOUND", "UNBOUND") or \
            square not in ("", "0", "1") or len(search) > 120:
        raise CatalogError("素材筛选参数不正确。")
    if kind:
        query = query.filter(kind=kind)
    if search:
        query = query.filter(original_name__icontains=search)
    if square == "1":
        query = query.filter(kind=Asset.Kind.IMAGE, width=F("height"))
    if binding == "UNBOUND":
        query = query.filter(id__in=Subquery(unbound.values("id")))
    elif binding == "BOUND":
        query = query.exclude(id__in=Subquery(unbound.values("id")))
    total = query.count()
    assets = list(query.order_by("-created_at", "-id")[(page - 1) * size:page * size])
    orphan_ids = set(unbound.filter(id__in=[row.id for row in assets]).values_list("id", flat=True))
    items = []
    for asset in assets:
        status = "READY"
        try:
            if not asset_path(asset).is_file():
                status = "MISSING"
        except (CatalogError, OSError):
            status = "MISSING"
        if status == "READY" and asset.id in orphan_ids and \
                asset.created_at < timezone.now() - settings.PRODUCT_MEDIA_ORPHAN_TTL:
            status = "EXPIRED"
        items.append({**asset_data(asset), "sha256": asset.sha256, "createdAt": asset.created_at.isoformat(),
            "bindingStatus": "UNBOUND" if asset.id in orphan_ids else "BOUND", "availability": status})
    return {"items": items, "total": total, "page": page, "pageSize": size}


def references_page(request, asset_id, granted):
    if set(request.GET) - {"page", "pageSize"}:
        raise CatalogError("引用查询含有不支持的参数。")
    page, size = pagination(request)
    queries = reference_queries(asset_id)
    for field, role in (("main_image_id", "MAIN_IMAGE"), ("video_id", "VIDEO")):
        queries.append(reference_query(Product.objects.filter(**{field: asset_id}), domain="CATALOG",
            object_id="id", label="name", role=role, version="revision", state="BOUND"))
    queries.append(reference_query(ProductGalleryImage.objects.filter(asset_id=asset_id), domain="CATALOG",
        object_id="product_id", label="product__name", role="GALLERY", version="product__revision", state="BOUND"))
    query = queries[0].union(*queries[1:], all=True).order_by("domain", "object_id", "ref_version", "role")
    total = query.count()
    items = []
    for row in query[(page - 1) * size:page * size]:
        permission = {"CATALOG": "catalog.read", "PAGE": "page.read", "STARTUP": "startup.read",
                      "NAVIGATION": "navigation.read", "CUSTOMER_SERVICE": "customer_service.read"}[row["domain"]]
        allowed = permission in granted
        items.append({"domain": row["domain"], "objectId": row["object_id"] if allowed else None,
            "label": row["label"] if allowed else "引用受权限保护", "role": row["role"],
            "version": row["ref_version"], "state": row["state"]})
    return {"items": items, "total": total, "page": page, "pageSize": size}
