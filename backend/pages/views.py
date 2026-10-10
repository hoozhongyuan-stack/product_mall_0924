"""Home and independent-page draft, preview, atomic publication and public APIs."""

import hashlib
import json

from django.db import connection, transaction

from common.http import offset_response, parse_json_object, method
from accounts.security import audit, confirm_action, error, require, require_live, response
from catalog.page_targets import assets_exist
from catalog.asset_access import authorize_asset_binding
from catalog.validation import CatalogError

from .models import (MicroPage, PageConfigVersion, PageDraftAsset, PagePublication,
                     PagePublishRequest, PageVersionAsset)
from .validation import PageConfigError, referenced_assets, validate_config
from .runtime import check_runtime, public_config, component_data
from .editor_v3 import sharing


def _page():
    return MicroPage.objects.get(page_type="HOME")


def _micro_page(page_id):
    return MicroPage.objects.filter(pk=page_id, page_type="MICRO").first()


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise PageConfigError("页面名称须为 1—80 个字符。")
    return value.strip()


def _default_micro_config():
    return {"schemaVersion": 1, "pageType": "MICRO",
            "theme": {"pageBackgroundColor": "#F7F5F1", "headerBackgroundColor": "#FFFFFF",
                      "brandTextColor": "#25221F"}, "components": []}


def _body(request):
    return parse_json_object(request, max_bytes=262144, error_type=PageConfigError)


def _revision(value):
    revision = value.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise PageConfigError("expectedRevision 必须是正整数。")
    return revision


def _failure(request, exc):
    return error(request, exc.status, exc.code, str(exc))


def _validate_for_publication(page):
    try:
        return validate_config(page.draft_config, publishing=True, page_type=page.page_type,
                               page_id=page.id)
    except (PageConfigError, CatalogError) as exc:
        if exc.status == 400:
            raise PageConfigError(str(exc), "PUBLISH_TARGET_INVALID", 422) from exc
        raise


def _draft_data(page):
    publication = PagePublication.objects.select_related("current_version").get(page=page)
    published = publication.current_version
    return {"pageId": str(page.id), "name": page.name, "revision": page.draft_revision,
            "config": page.draft_config, "publishedRevision": published.revision if published else None,
            "publishedVersionId": str(published.id) if published else None,
            "publicationRevision": publication.revision}


def _version_data(version):
    return {"versionId": str(version.id), "revision": version.revision, "config": version.config_json}


def _lock_publication_graph():
    """Serialize HOME/MICRO publication graph changes within the current transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError("页面发布图锁必须在事务内获取。")
    # The two-key PostgreSQL advisory lock namespace is MALL/PAGE. Every page
    # publication path must acquire this lock before reading published links.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [0x4D414C4C, 0x50414745])


def home_draft_view(request):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, bad = require(request, "page.read" if request.method == "GET" else "page.edit")
    if bad:
        return bad
    return _draft_view(request, _page(), actor, with_name=False)


def _draft_view(request, selected_page, actor, *, with_name):
    if request.method == "GET":
        return response(request, _draft_data(selected_page))
    try:
        values = _body(request)
        expected_fields = {"expectedRevision", "config", "name"} if with_name else {"expectedRevision", "config"}
        if set(values) != expected_fields:
            raise PageConfigError("草稿参数不正确。")
        revision = _revision(values)
        name = _name(values["name"]) if with_name else selected_page.name
        config = validate_config(values["config"], page_type=selected_page.page_type,
                                 page_id=selected_page.id)
        asset_ids = referenced_assets(config)
        with transaction.atomic():
            page = MicroPage.objects.select_for_update().get(pk=selected_page.pk)
            if page.draft_revision != revision:
                return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                             [{"currentRevision": page.draft_revision}])
            if not assets_exist(asset_ids):
                raise PageConfigError("草稿引用的素材不存在。")
            actor, bad = require_live(request, "page.edit")
            if bad:
                return bad
            authorize_asset_binding(actor, asset_ids,
                PageDraftAsset.objects.filter(page=page).values_list("asset_id", flat=True))
            before = {"revision": revision}
            page.draft_config = config
            page.name = name
            page.draft_revision += 1
            page.save(update_fields=["name", "draft_config", "draft_revision", "updated_at"])
            PageDraftAsset.objects.filter(page=page).delete()
            PageDraftAsset.objects.bulk_create(
                [PageDraftAsset(page=page, asset_id=asset_id) for asset_id in asset_ids])
            audit(request, "page.edit", "micro_page", page.id, actor,
                  before={**before, "name": selected_page.name},
                  after={"revision": page.draft_revision, "name": page.name})
        return response(request, _draft_data(page))
    except (PageConfigError, CatalogError) as exc:
        return _failure(request, exc)


def home_preview_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    _, bad = require(request, "page.read")
    if bad:
        return bad
    return _preview_view(request, _page())


def _preview_view(request, page):
    try:
        values = _body(request)
        if set(values) != {"expectedRevision"}:
            raise PageConfigError("预览参数不正确。")
        revision = _revision(values)
        if page.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                         [{"currentRevision": page.draft_revision}])
        from .editor_v4 import coupon_component_data
        coupon_data = {}
        if any(item["visible"] and item["type"] == "COUPON_LIST" for item in page.draft_config["components"]):
            _, bad = require(request, "coupon.read")
            if bad:
                return bad
        _validate_for_publication(page)
        if any(item["visible"] and item["type"] == "COUPON_LIST" for item in page.draft_config["components"]):
            coupon_data = coupon_component_data(page.draft_config)
        return response(request, {"revision": page.draft_revision, "config": page.draft_config,
                                  "componentData": component_data(page.draft_config), "couponData": coupon_data, "share": sharing(page.draft_config)})
    except (PageConfigError, CatalogError) as exc:
        return _failure(request, exc)


def home_publish_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    from .history import require_content
    actor, bad = require_content(request, "page", publish=True)
    if bad:
        return bad
    return _publish_view(request, _page(), actor)


def _publish_view(request, selected_page, actor):
    try:
        values = _body(request)
        if set(values) != {"expectedRevision", "expectedPublicationRevision"}:
            raise PageConfigError("发布参数不正确。")
        revision = _revision(values)
        from .history import publication_revision, require_content
        expected_publication = publication_revision(values)
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise PageConfigError("Idempotency-Key 必须是 8—128 位可打印 ASCII 字符。")
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with transaction.atomic():
            _lock_publication_graph()
            page = MicroPage.objects.select_for_update().get(pk=selected_page.pk)
            publication = PagePublication.objects.select_for_update().get(page=page)
            actor, bad = require_content(request, "page", live=True, publish=True)
            if bad:
                return bad
            existing = PagePublishRequest.objects.select_related("version").filter(
                page=page, actor=actor, key=key).first()
            if existing:
                if existing.request_digest != digest:
                    return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同的发布请求。")
                return response(request, existing.result)
            if page.draft_revision != revision:
                return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                             [{"currentRevision": page.draft_revision}])
            if publication.revision != expected_publication:
                return error(request, 409, "PUBLICATION_REVISION_CONFLICT", "线上版本已变化，请刷新。")
            if PageConfigVersion.objects.filter(page=page, revision=revision).exists():
                return error(request, 409, "PAGE_ALREADY_PUBLISHED", "当前草稿已发布，请先修改草稿。")
            if not assets_exist(referenced_assets(page.draft_config)):
                raise PageConfigError("草稿引用的素材不存在或不可用。")
            actor, bad = require_content(request, "page", live=True, publish=True)
            if bad:
                return bad
            _validate_for_publication(page)
            check_runtime(page.draft_config)
            bad = confirm_action(request, actor, "page.publish", page.id, revision)
            if bad:
                return bad
            previous = publication.current_version
            version = PageConfigVersion.objects.create(
                page=page, revision=revision, name=page.name,
                config_json=page.draft_config, published_by=actor)
            public_ids = referenced_assets(page.draft_config, visible_only=True)
            PageVersionAsset.objects.bulk_create([
                PageVersionAsset(version=version, asset_id=asset_id, is_public=asset_id in public_ids)
                for asset_id in referenced_assets(page.draft_config)])
            publication.current_version = version
            publication.revision += 1
            publication.save(update_fields=["current_version", "revision", "updated_at"])
            result = {**_version_data(version), "publicationRevision": publication.revision,
                      "draftRevision": page.draft_revision}
            PagePublishRequest.objects.create(page=page, actor=actor, key=key,
                                              request_digest=digest, version=version, result=result)
            audit(request, "page.publish", "micro_page", page.id, actor,
                  before={"versionId": str(previous.id) if previous else None},
                  after={"versionId": str(version.id), "revision": revision})
        return response(request, result)
    except (PageConfigError, CatalogError) as exc:
        return _failure(request, exc)


def public_home_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    publication = PagePublication.objects.select_related("current_version").filter(
        page__page_type="HOME", current_version__isnull=False).first()
    if not publication:
        return error(request, 404, "HOME_UNPUBLISHED", "首页尚未发布。")
    full_config = publication.current_version.config_json
    try:
        visible_config = public_config(request, full_config)
    except PageConfigError as exc:
        return _failure(request, exc)
    return response(request, {"versionId": str(publication.current_version_id),
                              "config": visible_config, "componentData": component_data(visible_config), "share": sharing(full_config)})


def pages_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "page.read" if request.method == "GET" else "page.edit")
    if bad:
        return bad
    if request.method == "GET":
        try:
            page_no = int(request.GET.get("page", "1"))
            page_size = int(request.GET.get("pageSize", "20"))
        except ValueError:
            return error(request, 400, "VALIDATION_FAILED", "分页参数不正确。")
        if not 1 <= page_no <= 100000 or not 1 <= page_size <= 100:
            return error(request, 400, "VALIDATION_FAILED", "分页参数不正确。")
        queryset = MicroPage.objects.filter(page_type="MICRO").select_related(
            "pagepublication__current_version").order_by("-updated_at", "-id")
        query = request.GET.get("q", "").strip()
        tag = request.GET.get("tag", "").strip()
        if len(query) > 80 or len(tag) > 20 or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
            return error(request, 400, "VALIDATION_FAILED", "页面筛选参数不正确。")
        if query:
            queryset = queryset.filter(name__icontains=query)
        if tag:
            queryset = queryset.filter(draft_config__metadata__tags__contains=[tag])
        total = queryset.count()
        rows = [{"pageId": str(page.id), "name": page.name, "revision": page.draft_revision,
                 "publishedRevision": page.pagepublication.current_version.revision
                 if page.pagepublication.current_version else None,
                 "updatedAt": page.updated_at.isoformat(),
                 "tags": page.draft_config.get("metadata", {}).get("tags", [])}
                for page in queryset[(page_no - 1) * page_size:page_no * page_size]]
        return offset_response(request, {"rows": rows, "page": page_no, "pageSize": page_size, "total": total})
    try:
        values = _body(request)
        if set(values) != {"name"}:
            raise PageConfigError("创建页面参数不正确。")
        name = _name(values["name"])
        with transaction.atomic():
            page = MicroPage.objects.create(page_type="MICRO", name=name,
                                            draft_config=_default_micro_config())
            PagePublication.objects.create(page=page)
            audit(request, "page.create", "micro_page", page.id, actor,
                  after={"name": name, "revision": page.draft_revision})
        return response(request, _draft_data(page), 201)
    except (PageConfigError, CatalogError) as exc:
        return _failure(request, exc)


def micro_draft_view(request, page_id):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, bad = require(request, "page.read" if request.method == "GET" else "page.edit")
    if bad:
        return bad
    page = _micro_page(page_id)
    if not page:
        return error(request, 404, "NOT_FOUND", "微页面不存在。")
    return _draft_view(request, page, actor, with_name=True)


def micro_preview_view(request, page_id):
    bad = method(request, "POST")
    if bad:
        return bad
    _, bad = require(request, "page.read")
    if bad:
        return bad
    page = _micro_page(page_id)
    return _preview_view(request, page) if page else error(request, 404, "NOT_FOUND", "微页面不存在。")


def micro_publish_view(request, page_id):
    bad = method(request, "POST")
    if bad:
        return bad
    from .history import require_content
    actor, bad = require_content(request, "page", publish=True)
    if bad:
        return bad
    page = _micro_page(page_id)
    return _publish_view(request, page, actor) if page else error(request, 404, "NOT_FOUND", "微页面不存在。")


def public_micro_view(request, page_id):
    bad = method(request, "GET")
    if bad:
        return bad
    publication = PagePublication.objects.select_related("page", "current_version").filter(
        page_id=page_id, page__page_type="MICRO", current_version__isnull=False).first()
    if not publication:
        return error(request, 404, "PAGE_UNPUBLISHED", "微页面尚未发布。")
    full_config = publication.current_version.config_json
    try:
        visible_config = public_config(request, full_config)
    except PageConfigError as exc:
        return _failure(request, exc)
    return response(request, {"pageId": str(publication.page_id),
                              "versionId": str(publication.current_version_id),
                              "name": publication.current_version.name, "config": visible_config, "componentData": component_data(visible_config), "share": sharing(full_config)})
