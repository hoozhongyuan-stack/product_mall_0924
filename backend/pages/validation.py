"""Strict, versioned home-page configuration validation."""

import re
import uuid

from catalog.page_targets import (category_is_available, page_image_is_available,
                                  product_is_available)
from .models import MicroPage, PagePublication


class PageConfigError(ValueError):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def fail(message, *, publishing=False):
    raise PageConfigError(message, "PUBLISH_TARGET_INVALID" if publishing else "VALIDATION_FAILED",
                          422 if publishing else 400)


def fields(value, required, optional=(), *, label="对象"):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        fail(f"{label}字段不正确。")


def string(value, label, maximum, *, nonempty=True):
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value.strip()):
        fail(f"{label}不正确。")
    return value


def identifier(value, label):
    try:
        return uuid.UUID(string(value, label, 36))
    except ValueError as exc:
        raise PageConfigError(f"{label}必须是 UUID。") from exc


def page_links(config, *, visible_only=False):
    """Find PAGE destinations in supported component link slots."""
    for component in config.get("components", []):
        if visible_only and not component.get("visible"):
            continue
        props = component.get("props", {})
        links = []
        if component.get("type") in {"NOTICE", "IMAGE"}:
            links = [props.get("link")]
        elif component.get("type") == "CAROUSEL":
            links = [slide.get("link") for slide in props.get("slides", [])]
        elif component.get("type") in {"NAVIGATION", "MOSAIC"}:
            links = [item.get("link") for item in props.get("items", [])]
        elif component.get("type") == "IMAGE_HOTZONE":
            links = [area.get("link") for area in props.get("areas", [])]
        for link in links:
            if isinstance(link, dict) and link.get("type") == "PAGE":
                yield link.get("targetId")


def _reaches_page(target_id, origin_id, graph_cache):
    if "graph" not in graph_cache:
        graph = {}
        for publication in PagePublication.objects.select_related("current_version").filter(
                current_version__isnull=False):
            destinations = set()
            for link in page_links(publication.current_version.config_json, visible_only=True):
                try:
                    destinations.add(uuid.UUID(link))
                except (TypeError, ValueError, AttributeError):
                    continue
            graph[publication.page_id] = destinations
        graph_cache["graph"] = graph
    pending = [target_id]
    seen = set()
    while pending:
        current = pending.pop()
        if current == origin_id:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(graph_cache["graph"].get(current, ()))
    return False


def check_link(link, *, publishing, page_id=None, graph_cache=None):
    fields(link, {"type", "targetId"}, label="链接")
    kind = link["type"]
    target = link["targetId"]
    if kind == "FUNCTION":
        if not isinstance(target, str) or target not in {"SEARCH", "CATALOG"}:
            fail("功能链接目标不支持。", publishing=publishing)
        return
    if kind == "PAGE":
        target_id = identifier(target, "链接目标 ID")
        if page_id and target_id == page_id:
            fail("页面不能链接到自身。", publishing=publishing)
        target_page = MicroPage.objects.filter(id=target_id, page_type="MICRO").first()
        if not target_page:
            fail("微页面链接目标不存在。", publishing=publishing)
        if publishing:
            if not PagePublication.objects.filter(page=target_page, current_version__isnull=False).exists():
                fail("微页面链接目标尚未发布。", publishing=True)
            if page_id and _reaches_page(target_id, page_id, graph_cache if graph_cache is not None else {}):
                fail("微页面链接会形成循环。", publishing=True)
        return
    target_id = identifier(target, "链接目标 ID")
    if kind == "CATEGORY":
        exists = category_is_available(target_id)
    elif kind == "PRODUCT":
        exists = product_is_available(target_id)
    else:
        fail("链接类型不支持。", publishing=publishing)
    if publishing and not exists:
        fail("链接目标不可用或尚未上架。", publishing=True)


def asset_id(value, *, publishing):
    if value in (None, "") and not publishing:
        return None
    asset_uuid = identifier(value, "素材 ID")
    if publishing:
        if not page_image_is_available(asset_uuid):
            fail("页面图片素材不可用。", publishing=True)
    return asset_uuid


def component_assets(component):
    props = component["props"]
    if component["type"] == "CAROUSEL":
        return {str(slide["assetId"]) for slide in props.get("slides", []) if slide.get("assetId")}
    if component["type"] in {"NAVIGATION", "MOSAIC"}:
        return {str(item["assetId"]) for item in props.get("items", []) if item.get("assetId")}
    if component["type"] in {"IMAGE_HOTZONE", "IMAGE"}:
        return {str(props["assetId"])} if props.get("assetId") else set()
    return set()


def referenced_assets(config, *, visible_only=False):
    assets = {item for component in config["components"] if not visible_only or component["visible"]
              for item in component_assets(component)}
    cover = config.get("metadata", {}).get("share", {}).get("coverAssetId")
    return assets | ({cover} if cover else set())


def validate_component(component, *, publishing, page_id=None, graph_cache=None, schema_version=1):
    fields(component, {"componentId", "type", "sortOrder", "visible", "props"},
           {"appearance"} if schema_version >= 2 else set(), label="组件")
    if "appearance" in component:
        from .editor_validation import appearance
        appearance(component["appearance"])
    string(component["componentId"], "组件 ID", 64)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", component["componentId"]):
        fail("组件 ID 仅允许字母、数字、短横线和下划线。")
    if type(component["sortOrder"]) is not int or not 0 <= component["sortOrder"] <= 10000:
        fail("组件顺序不正确。")
    if type(component["visible"]) is not bool:
        fail("组件显隐不正确。")
    kind, props = component["type"], component["props"]
    if kind == "SEARCH":
        fields(props, set(), {"placeholder"}, label="搜索组件")
        if "placeholder" in props:
            string(props["placeholder"], "搜索提示", 60, nonempty=False)
    elif kind == "NOTICE":
        fields(props, {"text"} if publishing else set(), {"text", "link"}, label="公告组件")
        if "text" in props:
            string(props["text"], "公告文案", 300, nonempty=publishing)
        if props.get("link") is not None:
            check_link(props["link"], publishing=publishing, page_id=page_id,
                       graph_cache=graph_cache)
    elif kind == "CAROUSEL":
        fields(props, {"slides"} if publishing else set(), {"slides"}, label="轮播组件")
        slides = props.get("slides", [])
        if not isinstance(slides, list) or len(slides) > 10 or (publishing and not slides):
            fail("轮播图数量不正确。", publishing=publishing)
        for slide in slides:
            fields(slide, {"assetId"} if publishing else set(), {"assetId", "link"}, label="轮播图")
            asset_id(slide.get("assetId"), publishing=publishing)
            if slide.get("link") is not None:
                check_link(slide["link"], publishing=publishing, page_id=page_id,
                           graph_cache=graph_cache)
    elif kind == "IMAGE_HOTZONE":
        fields(props, {"assetId", "areas"} if publishing else set(),
               {"assetId", "areas"}, label="热区组件")
        asset_id(props.get("assetId"), publishing=publishing)
        areas = props.get("areas", [])
        if not isinstance(areas, list) or len(areas) > 20 or (publishing and not areas):
            fail("热区数量不正确。", publishing=publishing)
        for area in areas:
            required = {"x", "y", "width", "height", "link"} if publishing else set()
            fields(area, required, {"x", "y", "width", "height", "link"}, label="热区")
            for key in ("x", "y", "width", "height"):
                if key in area and (type(area[key]) not in (int, float) or not 0 <= area[key] <= 1):
                    fail("热区坐标超出图片范围。")
            if all(key in area for key in ("x", "y", "width", "height")) and (
                    (publishing and (area["width"] <= 0 or area["height"] <= 0)) or
                    area["x"] + area["width"] > 1 or area["y"] + area["height"] > 1):
                fail("热区坐标超出图片范围。")
            if publishing and area.get("link") is None:
                fail("热区必须设置链接。", publishing=True)
            if area.get("link") is not None:
                check_link(area["link"], publishing=publishing, page_id=page_id,
                           graph_cache=graph_cache)
    elif kind == "DIVIDER":
        fields(props, {"style"} if publishing else set(), {"style"}, label="分隔组件")
        if "style" in props and (not isinstance(props["style"], str) or
                                 props["style"] not in {"SOLID", "DASHED", "SPACE"}):
            fail("分隔样式不支持。")
    elif kind == "FILING":
        fields(props, {"recordNo"} if publishing else set(), {"recordNo"}, label="备案组件")
        number = string(props.get("recordNo", ""), "备案号", 100, nonempty=publishing)
        if publishing and ("示例" in number or "待" in number or "占位" in number):
            fail("备案号尚未由运营提供。", publishing=True)
    elif schema_version == 4 and kind == "COUPON_LIST":
        from .editor_v4 import validate_coupons
        validate_coupons(props, publishing=publishing)
    elif schema_version >= 3 and kind in ("MOSAIC", "SPACER"):
        from .editor_v3 import validate_layout
        validate_layout(component, publishing=publishing, page_id=page_id, graph_cache=graph_cache)
    elif schema_version >= 2:
        from .editor_validation import validate_new
        validate_new(component, publishing=publishing, page_id=page_id, graph_cache=graph_cache)
    else:
        fail("组件类型不支持。")


def validate_config(config, *, publishing=False, page_type="HOME", page_id=None):
    fields(config, {"schemaVersion", "pageType", "theme", "components"},
           {"metadata"} if isinstance(config, dict) and config.get("schemaVersion") in (3, 4) else set(), label="页面配置")
    if type(config["schemaVersion"]) is not int or config["schemaVersion"] not in (1, 2, 3, 4) or config["pageType"] != page_type:
        fail("页面版本或类型不支持。")
    if "metadata" in config:
        from .editor_v3 import validate_metadata
        validate_metadata(config["metadata"], publishing=publishing)
    theme = config["theme"]
    fields(theme, {"pageBackgroundColor", "headerBackgroundColor", "brandTextColor"}, label="主题")
    for value in theme.values():
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            fail("主题色必须使用 #RRGGBB。")
    components = config["components"]
    if not isinstance(components, list) or len(components) > 40:
        fail("组件数量不正确。")
    graph_cache = {}
    for component in components:
        if not isinstance(component, dict):
            fail("组件须为对象。")
        validate_component(component, publishing=publishing and component.get("visible") is True,
                           page_id=page_id, graph_cache=graph_cache, schema_version=config["schemaVersion"])
    if len({item["componentId"] for item in components}) != len(components) or \
            len({item["sortOrder"] for item in components}) != len(components):
        fail("组件 ID 和顺序不得重复。")
    return config
