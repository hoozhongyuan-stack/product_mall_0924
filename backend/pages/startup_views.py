"""Startup media draft, preview, publication and public current-version APIs."""

import hashlib
import json
import uuid

from django.db import transaction

from common.http import parse_json_object, method
from accounts.security import audit, confirm_action, error, require, require_live, response
from catalog.media import asset_path, png_dimensions, available_asset, lock_available_assets
from catalog.models import Asset
from catalog.asset_access import authorize_asset_binding
from catalog.validation import CatalogError

from .validation import PageConfigError
from .models import (StartupConfig, StartupConfigVersion, StartupPublication,
                     StartupPublishRequest)


class StartupError(Exception):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        self.code = code
        self.status = status
        super().__init__(message)


def _body(request):
    return parse_json_object(request, max_bytes=8192, error_type=StartupError)


def _revision(value):
    revision = value.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise StartupError("expectedRevision 必须是正整数。")
    return revision


def _asset_id(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise StartupError(f"{label} ID 格式不正确。", "MEDIA_INVALID")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise StartupError(f"{label} ID 格式不正确。", "MEDIA_INVALID") from exc


def _assets(gif_id, fallback_id, *, required=False, lock=False):
    if required and (not gif_id or not fallback_id):
        raise StartupError("发布前须配置 GIF 和静态兜底图。", "MEDIA_INVALID", 422)
    if gif_id and gif_id == fallback_id:
        raise StartupError("GIF 与静态兜底图须使用不同素材。", "MEDIA_INVALID")
    ids = [value for value in (gif_id, fallback_id) if value]
    queryset = Asset.objects.filter(id__in=ids)
    if lock:
        try:
            found = lock_available_assets(ids)
        except CatalogError as exc:
            raise StartupError(str(exc), exc.code if exc.status >= 500 else "MEDIA_INVALID",
                               exc.status if exc.status >= 500 else (422 if required else 400)) from exc
    else:
        found = {asset.id: asset for asset in queryset}
    if len(found) != len(ids):
        raise StartupError("素材不存在或不可用。", "MEDIA_INVALID", 422 if required else 400)
    gif = found.get(gif_id)
    fallback = found.get(fallback_id)
    if gif and (gif.kind != Asset.Kind.GIF or gif.content_type != "image/gif"):
        raise StartupError("启动动图须为 GIF。", "MEDIA_INVALID", 422 if required else 400)
    if fallback and (fallback.kind != Asset.Kind.IMAGE or
                     fallback.content_type not in ("image/png", "image/jpeg")):
        raise StartupError("兜底图须为静态 PNG 或 JPEG。", "MEDIA_INVALID", 422 if required else 400)
    if any(not available_asset(asset) for asset in found.values()):
        raise StartupError("素材文件丢失，请重新上传。", "MEDIA_INVALID", 422 if required else 400)
    if fallback and fallback.content_type == "image/png":
        try:
            png_dimensions(asset_path(fallback).read_bytes())
        except CatalogError as exc:
            raise StartupError("兜底图须为有效的静态 PNG。", "MEDIA_INVALID",
                               422 if required else 400) from exc
    return gif, fallback


def _asset_url(asset_id, public=False):
    namespace = "app" if public else "admin"
    return f"/api/v1/{namespace}/assets/{asset_id}/file" if asset_id else None


def _draft_data(config):
    publication = StartupPublication.objects.select_related("current_version").get(config=config)
    version = publication.current_version
    return {"revision": config.draft_revision,
            "gifAssetId": str(config.gif_asset_id) if config.gif_asset_id else None,
            "fallbackAssetId": str(config.fallback_asset_id) if config.fallback_asset_id else None,
            "publishedRevision": version.revision if version else None,
            "publishedVersionId": str(version.id) if version else None,
            "publicationRevision": publication.revision}


def _preview_data(config):
    return {"revision": config.draft_revision,
            "gifAssetId": str(config.gif_asset_id),
            "fallbackAssetId": str(config.fallback_asset_id),
            "gifUrl": _asset_url(config.gif_asset_id),
            "fallbackUrl": _asset_url(config.fallback_asset_id)}


def _version_data(version):
    return {"versionId": str(version.id), "revision": version.revision,
            "gifAssetId": str(version.gif_asset_id),
            "fallbackAssetId": str(version.fallback_asset_id),
            "gifUrl": _asset_url(version.gif_asset_id, public=True),
            "fallbackUrl": _asset_url(version.fallback_asset_id, public=True)}


def draft_view(request):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, bad = require(request, "startup.read" if request.method == "GET" else "startup.edit")
    if bad:
        return bad
    if request.method == "GET":
        return response(request, _draft_data(StartupConfig.objects.get(pk=1)))
    try:
        values = _body(request)
        if set(values) != {"expectedRevision", "gifAssetId", "fallbackAssetId"}:
            raise StartupError("草稿参数不正确。")
        revision = _revision(values)
        gif_id = _asset_id(values["gifAssetId"], "GIF")
        fallback_id = _asset_id(values["fallbackAssetId"], "兜底图")
        with transaction.atomic():
            config = StartupConfig.objects.select_for_update().get(pk=1)
            if config.draft_revision != revision:
                return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                             [{"currentRevision": config.draft_revision}])
            _assets(gif_id, fallback_id, lock=True)
            actor, bad = require_live(request, "startup.edit")
            if bad:
                return bad
            authorize_asset_binding(actor, [item for item in (gif_id, fallback_id) if item],
                [item for item in (config.gif_asset_id, config.fallback_asset_id) if item])
            before = {"revision": revision, "gifAssetId": str(config.gif_asset_id) if config.gif_asset_id else None,
                      "fallbackAssetId": str(config.fallback_asset_id) if config.fallback_asset_id else None}
            config.gif_asset_id = gif_id
            config.fallback_asset_id = fallback_id
            config.draft_revision += 1
            config.save(update_fields=["gif_asset", "fallback_asset", "draft_revision", "updated_at"])
            audit(request, "startup.edit", "startup_config", "startup", actor, before=before,
                  after={"revision": config.draft_revision, "gifAssetId": str(gif_id) if gif_id else None,
                         "fallbackAssetId": str(fallback_id) if fallback_id else None})
        return response(request, _draft_data(config))
    except (StartupError, CatalogError, PageConfigError) as exc:
        return error(request, exc.status, exc.code, str(exc))


def preview_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    _, bad = require(request, "startup.read")
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) != {"expectedRevision"}:
            raise StartupError("预览参数不正确。")
        revision = _revision(values)
        config = StartupConfig.objects.get(pk=1)
        if config.draft_revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                         [{"currentRevision": config.draft_revision}])
        _assets(config.gif_asset_id, config.fallback_asset_id, required=True)
        return response(request, _preview_data(config))
    except (StartupError, CatalogError, PageConfigError) as exc:
        return error(request, exc.status, exc.code, str(exc))


def publish_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    from .history import require_content, publication_revision
    from .views import _lock_publication_graph
    actor, bad = require_content(request, "startup", publish=True)
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) != {"expectedRevision", "expectedPublicationRevision"}:
            raise StartupError("发布参数不正确。")
        revision = _revision(values)
        expected_publication = publication_revision(values)
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise StartupError("Idempotency-Key 必须是 8—128 位可打印 ASCII 字符。")
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with transaction.atomic():
            _lock_publication_graph()
            config = StartupConfig.objects.select_for_update().get(pk=1)
            publication = StartupPublication.objects.select_for_update().get(config=config)
            actor, bad = require_content(request, "startup", live=True, publish=True)
            if bad:
                return bad
            existing = StartupPublishRequest.objects.select_related("version").filter(actor=actor, key=key).first()
            if existing:
                if existing.request_digest != digest:
                    return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同的发布请求。")
                return response(request, existing.result)
            if config.draft_revision != revision:
                return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                             [{"currentRevision": config.draft_revision}])
            if publication.revision != expected_publication:
                return error(request, 409, "PUBLICATION_REVISION_CONFLICT", "线上版本已变化，请刷新。")
            if StartupConfigVersion.objects.filter(revision=revision).exists():
                return error(request, 409, "STARTUP_ALREADY_PUBLISHED", "当前草稿已发布，请先修改草稿。")
            _assets(config.gif_asset_id, config.fallback_asset_id, required=True, lock=True)
            actor, bad = require_content(request, "startup", live=True, publish=True)
            if bad:
                return bad
            bad = confirm_action(request, actor, "startup.publish", "startup", revision)
            if bad:
                return bad
            previous = publication.current_version
            version = StartupConfigVersion.objects.create(
                revision=revision, gif_asset_id=config.gif_asset_id,
                fallback_asset_id=config.fallback_asset_id, published_by=actor)
            publication.current_version = version
            publication.revision += 1
            publication.save(update_fields=["current_version", "revision", "updated_at"])
            result = {**_version_data(version), "publicationRevision": publication.revision,
                      "draftRevision": config.draft_revision}
            StartupPublishRequest.objects.create(actor=actor, key=key, request_digest=digest, version=version,
                                                 result=result)
            audit(request, "startup.publish", "startup_config", "startup", actor,
                  before={"versionId": str(previous.id) if previous else None},
                  after={"versionId": str(version.id), "revision": revision})
        return response(request, result)
    except (StartupError, CatalogError, PageConfigError) as exc:
        return error(request, exc.status, exc.code, str(exc))


def public_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    publication = StartupPublication.objects.select_related("current_version").get(config_id=1)
    if not publication.current_version:
        return error(request, 404, "STARTUP_UNPUBLISHED", "启动内容尚未发布。")
    return response(request, _version_data(publication.current_version))
