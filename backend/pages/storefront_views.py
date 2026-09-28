"""Independent navigation and customer-service drafts, publication and history."""
import hashlib
import json
import uuid

from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie

from accounts.security import audit, confirm_action, error, require, require_live, response
from accounts.views import method
from catalog.asset_access import authorize_asset_binding
from catalog.media import asset_path, inspect_file, lock_available_assets
from catalog.models import Asset
from catalog.storage import storage_unavailable
from catalog.validation import CatalogError
from .history import idempotency_key, pagination, publication_revision
from .models import (StorefrontConfig, StorefrontDraftAsset, StorefrontOperation,
                     StorefrontPublication, StorefrontVersion, StorefrontVersionAsset)
from .storefront_validation import asset_ids, defaults, normalize, validate_assets
from .validation import PageConfigError


DOMAINS = {"navigation": "navigation", "customer-service": "customer_service"}


def _body(request):
    if request.content_type != "application/json" or len(request.body) > 8192:
        raise PageConfigError("请发送不超过 8 KB 的 JSON 请求。")
    try:
        values = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PageConfigError("JSON 格式不正确。") from exc
    if not isinstance(values, dict):
        raise PageConfigError("请求内容须为对象。")
    return values


def _revision(values):
    revision = values.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise PageConfigError("expectedRevision 必须是正整数。")
    return revision


def _access(request, domain, capability, *, live=False):
    checker = require_live if live else require
    actor, bad = checker(request, f"{domain}.read")
    if bad is None and capability != "read":
        actor, bad = checker(request, f"{domain}.{capability}")
    return actor, bad


def _domain(segment):
    try:
        return DOMAINS[segment]
    except KeyError as exc:
        raise PageConfigError("配置域不存在。", "NOT_FOUND", 404) from exc


def _config(domain, *, lock=False):
    query = StorefrontConfig.objects.select_for_update() if lock else StorefrontConfig.objects
    return query.get(pk=domain)


def _publication(config, *, lock=False):
    query = StorefrontPublication.objects.select_for_update() if lock else StorefrontPublication.objects
    return (query if lock else query.select_related("current_version")).get(config=config)


def _metadata(version, publication):
    return {"versionId": str(version.id), "revision": version.revision,
            "name": "底部导航" if version.config_id == "navigation" else "客服悬浮配置",
            "publishedAt": version.published_at.isoformat(),
            "publishedBy": version.published_by.display_name,
            "isCurrent": version.id == publication.current_version_id}


def _draft_data(config):
    publication = _publication(config)
    version = publication.current_version
    return {"revision": config.draft_revision, "config": config.draft_config,
            "publishedRevision": version.revision if version else None,
            "publishedVersionId": str(version.id) if version else None,
            "publicationRevision": publication.revision}


def _digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _locked_assets(domain, config):
    identifiers = asset_ids(domain, config)
    try:
        assets = lock_available_assets([uuid.UUID(identifier) for identifier in identifiers])
    except CatalogError as exc:
        if exc.status >= 500:
            raise
        raise PageConfigError("素材不存在、已过期或文件丢失，请重新上传。", "MEDIA_INVALID", 422) from exc
    validate_assets(domain, config, assets)
    for asset in assets.values():
        try:
            path = asset_path(asset)
            content = path.read_bytes()
            content_type, _, width, height = inspect_file(Asset.Kind.IMAGE, path)
        except CatalogError as exc:
            if exc.status >= 500:
                raise
            raise PageConfigError("图片文件已损坏，请重新上传。", "MEDIA_INVALID", 422) from exc
        if (content_type, width, height, len(content), hashlib.sha256(content).hexdigest()) != (
                asset.content_type, asset.width, asset.height, asset.byte_size, asset.sha256):
            raise PageConfigError("图片文件与素材记录不一致，请重新上传。", "MEDIA_INVALID", 422)
    return assets


def _draft(request, domain):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, bad = _access(request, domain, "read" if request.method == "GET" else "edit")
    if bad:
        return bad
    if request.method == "GET":
        return response(request, _draft_data(_config(domain)))
    values = _body(request)
    if set(values) != {"expectedRevision", "config"}:
        raise PageConfigError("草稿参数不正确。")
    revision = _revision(values)
    normalized = normalize(domain, values["config"])
    with transaction.atomic():
        config = _config(domain, lock=True)
        if config.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新。")
        _locked_assets(domain, normalized)
        actor, bad = _access(request, domain, "edit", live=True)
        if bad:
            return bad
        previous = asset_ids(domain, config.draft_config)
        authorize_asset_binding(actor, asset_ids(domain, normalized), previous)
        before = {"revision": revision, "config": config.draft_config}
        config.draft_config = normalized
        config.draft_revision += 1
        config.save(update_fields=["draft_config", "draft_revision", "updated_at"])
        StorefrontDraftAsset.objects.filter(config=config).delete()
        StorefrontDraftAsset.objects.bulk_create([
            StorefrontDraftAsset(config=config, asset_id=identifier)
            for identifier in sorted(asset_ids(domain, normalized))])
        audit(request, f"{domain}.edit", "storefront_config", domain, actor,
              before=before, after={"revision": config.draft_revision, "config": normalized})
    return response(request, _draft_data(config))


def _preview(request, domain):
    bad = method(request, "POST")
    if bad:
        return bad
    _, bad = _access(request, domain, "read")
    if bad:
        return bad
    values = _body(request)
    if set(values) != {"expectedRevision"}:
        raise PageConfigError("预览参数不正确。")
    revision = _revision(values)
    with transaction.atomic():
        config = _config(domain, lock=True)
        if config.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新。")
        normalized = normalize(domain, config.draft_config)
        _locked_assets(domain, normalized)
        _, bad = _access(request, domain, "read", live=True)
        if bad:
            return bad
        return response(request, {"revision": config.draft_revision, "config": normalized})


def _publish(request, domain):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _access(request, domain, "publish")
    if bad:
        return bad
    values = _body(request)
    if set(values) != {"expectedRevision", "expectedPublicationRevision"}:
        raise PageConfigError("发布参数不正确。")
    revision, expected_publication = _revision(values), publication_revision(values)
    key = idempotency_key(request)
    digest = _digest(values)
    with transaction.atomic():
        config = _config(domain, lock=True)
        publication = _publication(config, lock=True)
        actor, bad = _access(request, domain, "publish", live=True)
        if bad:
            return bad
        prior = StorefrontOperation.objects.filter(config=config, actor=actor, action="publish", key=key).first()
        if prior:
            if prior.request_digest != digest:
                return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求。")
            return response(request, prior.result)
        if config.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新。")
        if publication.revision != expected_publication:
            return error(request, 409, "PUBLICATION_REVISION_CONFLICT", "线上版本已变化，请刷新。")
        if StorefrontVersion.objects.filter(config=config, revision=revision).exists():
            return error(request, 409, "ALREADY_PUBLISHED", "当前草稿已发布，请先修改草稿。")
        normalized = normalize(domain, config.draft_config)
        _locked_assets(domain, normalized)
        actor, bad = _access(request, domain, "publish", live=True)
        if bad:
            return bad
        bad = confirm_action(request, actor, f"{domain}.publish", domain, revision)
        if bad:
            return bad
        previous = publication.current_version_id
        version = StorefrontVersion.objects.create(config=config, revision=revision,
                                                   config_json=normalized, published_by=actor)
        StorefrontVersionAsset.objects.bulk_create([
            StorefrontVersionAsset(version=version, asset_id=identifier)
            for identifier in sorted(asset_ids(domain, normalized))])
        publication.current_version = version
        publication.revision += 1
        publication.save(update_fields=["current_version", "revision", "updated_at"])
        result = {"versionId": str(version.id), "revision": version.revision,
                  "publicationRevision": publication.revision, "draftRevision": config.draft_revision}
        StorefrontOperation.objects.create(config=config, actor=actor, action="publish", key=key,
                                           request_digest=digest, result=result, version=version)
        audit(request, f"{domain}.publish", "storefront_config", domain, actor,
              before={"versionId": str(previous) if previous else None}, after=result)
    return response(request, result)


def _versions(request, domain, version_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = _access(request, domain, "read")
    if bad:
        return bad
    config = _config(domain)
    publication = _publication(config)
    query = StorefrontVersion.objects.filter(config=config).select_related("published_by").order_by("-revision", "-id")
    if version_id is not None:
        version = query.filter(pk=version_id).first()
        if version is None:
            return error(request, 404, "NOT_FOUND", "历史版本不存在。")
        return response(request, {**_metadata(version, publication), "config": version.config_json})
    page, size = pagination(request)
    return response(request, {"list": [_metadata(version, publication) for version in
                               query[(page - 1) * size:page * size]], "total": query.count(),
                              "page": page, "pageSize": size,
                              "currentVersionId": str(publication.current_version_id)
                              if publication.current_version_id else None,
                              "publicationRevision": publication.revision,
                              "draftRevision": config.draft_revision})


def _rollback(request, domain):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _access(request, domain, "publish")
    if bad:
        return bad
    values = _body(request)
    if set(values) != {"expectedRevision", "expectedPublicationRevision", "versionId", "reason"}:
        raise PageConfigError("回退参数不正确。")
    revision, expected_publication = _revision(values), publication_revision(values)
    if not isinstance(values["versionId"], str):
        raise PageConfigError("versionId 必须是 UUID。")
    try:
        version_id = uuid.UUID(values["versionId"])
    except ValueError as exc:
        raise PageConfigError("versionId 必须是 UUID。") from exc
    if not isinstance(values["reason"], str) or not 1 <= len(values["reason"].strip()) <= 200:
        raise PageConfigError("回退原因须为 1—200 个字符。")
    key = idempotency_key(request)
    digest = _digest(values)
    with transaction.atomic():
        config = _config(domain, lock=True)
        publication = _publication(config, lock=True)
        actor, bad = _access(request, domain, "publish", live=True)
        if bad:
            return bad
        prior = StorefrontOperation.objects.filter(config=config, actor=actor, action="rollback", key=key).first()
        if prior:
            if prior.request_digest != digest:
                return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求。")
            return response(request, prior.result)
        if config.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新。")
        if publication.revision != expected_publication:
            return error(request, 409, "PUBLICATION_REVISION_CONFLICT", "线上版本已变化，请刷新。")
        version = StorefrontVersion.objects.filter(config=config, pk=version_id).first()
        if version is None:
            return error(request, 404, "NOT_FOUND", "历史版本不存在。")
        if version.id == publication.current_version_id:
            return error(request, 409, "ALREADY_CURRENT", "目标版本已经在线。")
        normalized = normalize(domain, version.config_json)
        _locked_assets(domain, normalized)
        actor, bad = _access(request, domain, "publish", live=True)
        if bad:
            return bad
        bad = confirm_action(request, actor, f"{domain}.rollback", f"{domain}:{version.id}",
                             publication.revision)
        if bad:
            return bad
        previous = publication.current_version_id
        publication.current_version = version
        publication.revision += 1
        publication.save(update_fields=["current_version", "revision", "updated_at"])
        result = {"versionId": str(version.id), "revision": version.revision,
                  "publicationRevision": publication.revision, "draftRevision": config.draft_revision}
        StorefrontOperation.objects.create(config=config, actor=actor, action="rollback", key=key,
                                           request_digest=digest, result=result, version=version)
        audit(request, f"{domain}.rollback", "storefront_config", domain, actor,
              before={"versionId": str(previous), "publicationRevision": publication.revision - 1},
              after={**result, "reason": values["reason"].strip()})
    return response(request, result)


@never_cache
@vary_on_cookie
def admin_view(request, segment, operation, version_id=None):
    try:
        domain = _domain(segment)
        if operation == "draft":
            return _draft(request, domain)
        if operation == "preview":
            return _preview(request, domain)
        if operation == "publish":
            return _publish(request, domain)
        if operation == "versions":
            return _versions(request, domain, version_id)
        if operation == "rollback":
            return _rollback(request, domain)
        raise PageConfigError("配置入口不存在。", "NOT_FOUND", 404)
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))
    except OSError as exc:
        failure = storage_unavailable(exc)
        return error(request, failure.status, failure.code, str(failure))


def _public_url(identifier):
    return f"/api/v1/app/assets/{identifier}/file" if identifier else None


def _public_domain(domain):
    publication = StorefrontPublication.objects.select_related("current_version").get(config_id=domain)
    version = publication.current_version
    config = version.config_json if version else defaults(domain)
    if domain == "navigation":
        return {"versionId": str(version.id) if version else None,
                "items": [{"key": item["key"], "label": item["label"],
                           "iconUrl": _public_url(item["iconAssetId"]),
                           "selectedIconUrl": _public_url(item["selectedIconAssetId"])}
                          for item in config["items"]]}
    enabled = config["enabled"]
    return {"versionId": str(version.id) if version else None, "enabled": enabled,
            "mode": config["mode"] if enabled else None,
            "phone": config["phone"] if enabled and config["mode"] == "PHONE" else None,
            "qrUrl": _public_url(config["qrAssetId"]) if enabled and config["mode"] == "QR" else None,
            "prompt": config["prompt"] if enabled else "",
            "iconUrl": _public_url(config["iconAssetId"]) if enabled else None}


@never_cache
def public_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    return response(request, {"navigation": _public_domain("navigation"),
                              "customerService": _public_domain("customer_service")})
