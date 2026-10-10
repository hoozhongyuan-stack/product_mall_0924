"""Private immutable content history and fenced, idempotent pointer rollback."""
import hashlib
import json
import uuid
from types import SimpleNamespace

from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie

from common.http import offset_response, method
from accounts.security import audit, confirm_action, error, require, require_live, response
from catalog.page_targets import assets_exist
from catalog.storage import storage_unavailable
from catalog.validation import CatalogError
from .models import (ContentRollbackRequest, MicroPage, PageConfigVersion, PagePublication,
                     StartupConfig, StartupConfigVersion, StartupPublication)
from .validation import PageConfigError, referenced_assets


def idempotency_key(request):
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise PageConfigError("Idempotency-Key 必须是 8—128 位可打印 ASCII 字符。")
    return key


def publication_revision(values):
    revision = values.get("expectedPublicationRevision")
    if type(revision) is not int or revision < 0:
        raise PageConfigError("expectedPublicationRevision 必须是非负整数。")
    return revision


def require_content(request, domain, *, live=False, publish=False):
    checker = require_live if live else require
    actor, bad = checker(request, f"{domain}.read")
    if bad is None and publish:
        actor, bad = checker(request, f"{domain}.publish")
    return actor, bad


def selected_content(domain, page_id=None, *, lock=False):
    if domain == "startup":
        query = StartupConfig.objects
        return query.select_for_update().get(pk=1) if lock else query.get(pk=1)
    query = MicroPage.objects.select_for_update() if lock else MicroPage.objects
    return query.filter(pk=page_id, page_type="MICRO").first() if page_id else query.get(page_type="HOME")


def publication_for(domain, content, *, lock=False):
    query = StartupPublication.objects if domain == "startup" else PagePublication.objects
    query = query.select_for_update() if lock else query
    return query.get(config=content) if domain == "startup" else query.get(page=content)


def versions_for(domain, content):
    query = StartupConfigVersion.objects.all() if domain == "startup" else PageConfigVersion.objects.filter(page=content)
    return query.select_related("published_by").order_by("-revision", "-id")


def version_metadata(version, publication):
    return {"versionId": str(version.id), "revision": version.revision,
            "name": getattr(version, "name", "启动配置"), "publishedAt": version.published_at.isoformat(),
            "publishedBy": version.published_by.display_name,
            "isCurrent": version.id == publication.current_version_id}


def pagination(request):
    if set(request.GET) - {"page", "pageSize"} or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
        raise PageConfigError("分页参数不正确。")
    try:
        page, size = int(request.GET.get("page", "1")), int(request.GET.get("pageSize", "20"))
    except ValueError:
        raise PageConfigError("分页参数不正确。") from None
    if not 1 <= page <= 100000 or not 1 <= size <= 100:
        raise PageConfigError("分页参数不正确。")
    return page, size


def history_response(request, domain, page_id, version_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require_content(request, domain)
    if bad:
        return bad
    try:
        content = selected_content(domain, page_id)
        if content is None:
            return error(request, 404, "NOT_FOUND", "页面不存在。")
        publication = publication_for(domain, content)
        versions = versions_for(domain, content)
        if version_id is not None:
            version = versions.filter(pk=version_id).first()
            if version is None:
                return error(request, 404, "NOT_FOUND", "历史版本不存在。")
            data = version_metadata(version, publication)
            if domain == "page":
                data["config"] = version.config_json
            else:
                from .startup_views import _asset_url
                data.update({"gifAssetId": str(version.gif_asset_id),
                             "fallbackAssetId": str(version.fallback_asset_id),
                             "gifUrl": _asset_url(version.gif_asset_id),
                             "fallbackUrl": _asset_url(version.fallback_asset_id)})
            return response(request, data)
        page, size = pagination(request)
        return offset_response(request, {"list": [version_metadata(row, publication) for row in
            versions[(page - 1) * size:page * size]], "total": versions.count(), "page": page, "pageSize": size,
            "currentVersionId": str(publication.current_version_id) if publication.current_version_id else None,
            "publicationRevision": publication.revision, "draftRevision": content.draft_revision})
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))


def rollback_values(request):
    from .views import _body, _revision
    values = _body(request)
    if set(values) != {"expectedRevision", "expectedPublicationRevision", "versionId", "reason"}:
        raise PageConfigError("回退参数不正确。")
    _revision(values)
    revision = values["expectedPublicationRevision"]
    if type(revision) is not int or revision < 0:
        raise PageConfigError("expectedPublicationRevision 必须是非负整数。")
    reason = values["reason"]
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200:
        raise PageConfigError("回退原因须为 1—200 个字符。")
    if not isinstance(values["versionId"], str):
        raise PageConfigError("versionId 必须是 UUID。")
    try:
        uuid.UUID(values["versionId"])
    except ValueError as exc:
        raise PageConfigError("versionId 必须是 UUID。") from exc
    return values


def validate_target(domain, content, version):
    if domain == "startup":
        from .startup_views import _assets
        _assets(version.gif_asset_id, version.fallback_asset_id, required=True, lock=True)
    else:
        from .views import _validate_for_publication
        from catalog.media import lock_available_assets
        try:
            lock_available_assets(referenced_assets(version.config_json))
        except CatalogError as exc:
            if exc.status >= 500:
                raise
            raise PageConfigError("历史版本素材不可用，请重新上传后发布。", "PUBLISH_TARGET_INVALID", 422) from exc
        historical = SimpleNamespace(id=content.id, page_type=content.page_type, draft_config=version.config_json)
        _validate_for_publication(historical)
        from .runtime import check_runtime
        check_runtime(version.config_json)


def rollback_response(request, domain, page_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require_content(request, domain, publish=True)
    if bad:
        return bad
    try:
        from .views import _lock_publication_graph
        from .startup_views import StartupError
        values = rollback_values(request)
        key = idempotency_key(request)
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False).encode()).hexdigest()
        with transaction.atomic():
            _lock_publication_graph()
            content = selected_content(domain, page_id, lock=True)
            if content is None:
                return error(request, 404, "NOT_FOUND", "页面不存在。")
            publication = publication_for(domain, content, lock=True)
            actor, bad = require_content(request, domain, live=True, publish=True)
            if bad:
                return bad
            object_id = "startup" if domain == "startup" else str(content.id)
            existing = ContentRollbackRequest.objects.filter(actor=actor, domain=domain,
                                                              object_id=object_id, key=key).first()
            if existing:
                if existing.request_digest != digest:
                    return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同的回退请求。")
                return response(request, existing.result)
            if content.draft_revision != values["expectedRevision"]:
                return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新。")
            if publication.revision != values["expectedPublicationRevision"]:
                return error(request, 409, "PUBLICATION_REVISION_CONFLICT", "线上版本已变化，请刷新。")
            target = versions_for(domain, content).filter(pk=values["versionId"]).first()
            if target is None:
                return error(request, 404, "NOT_FOUND", "历史版本不存在或属于其他页面。")
            if publication.current_version_id == target.id:
                return error(request, 409, "ALREADY_CURRENT", "目标版本已经在线。")
            validate_target(domain, content, target)
            actor, bad = require_content(request, domain, live=True, publish=True)
            if bad:
                return bad
            bad = confirm_action(request, actor, f"{domain}.rollback", object_id + ":" + str(target.id),
                                 publication.revision)
            if bad:
                return bad
            previous = publication.current_version_id
            publication.current_version = target
            publication.revision += 1
            publication.save(update_fields=["current_version", "revision", "updated_at"])
            result = {"versionId": str(target.id), "revision": target.revision,
                      "publicationRevision": publication.revision, "draftRevision": content.draft_revision}
            ContentRollbackRequest.objects.create(actor=actor, domain=domain, object_id=object_id,
                                                  key=key, request_digest=digest, result=result)
            audit(request, f"{domain}.rollback", "startup_config" if domain == "startup" else "micro_page",
                  object_id, actor, before={"versionId": str(previous) if previous else None,
                  "publicationRevision": publication.revision - 1}, after={**result, "reason": values["reason"].strip()})
        return response(request, result)
    except (PageConfigError, CatalogError, StartupError) as exc:
        return error(request, exc.status, exc.code, str(exc))
    except OSError as exc:
        failure = storage_unavailable(exc)
        return error(request, failure.status, failure.code, str(failure))


@never_cache
@vary_on_cookie
def page_versions_view(request, page_id=None, version_id=None):
    return history_response(request, "page", page_id, version_id)


@never_cache
@vary_on_cookie
def startup_versions_view(request, version_id=None):
    return history_response(request, "startup", None, version_id)


@never_cache
@vary_on_cookie
def page_rollback_view(request, page_id=None):
    return rollback_response(request, "page", page_id)


@never_cache
@vary_on_cookie
def startup_rollback_view(request):
    return rollback_response(request, "startup", None)
