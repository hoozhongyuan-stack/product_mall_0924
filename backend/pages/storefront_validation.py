"""Strict, closed configuration contract for the two storefront singletons."""
import re
import uuid

from catalog.models import Asset
from .validation import PageConfigError


NAVIGATION = (("HOME", "首页"), ("CATEGORY", "分类"),
              ("CART", "购物车"), ("ME", "我的"))
PHONE_PATTERN = re.compile(r"^1[3-9][0-9]{9}$")
ENTRY_KEY = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def navigation_v2(value):
    if set(value) != {"schemaVersion", "items"} or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 2:
        raise PageConfigError("导航配置版本不正确。")
    if not isinstance(value["items"], list) or not 1 <= len(value["items"]) <= 5:
        raise PageConfigError("底部导航须配置 1—5 个入口。")
    items, keys = [], set()
    for item in value["items"]:
        if not isinstance(item, dict) or set(item) != {"key", "label", "target", "iconAssetId", "selectedIconAssetId"}:
            raise PageConfigError("导航项参数不正确。")
        key, label, target = item["key"], item["label"], item["target"]
        if not isinstance(key, str) or not ENTRY_KEY.fullmatch(key) or key in keys:
            raise PageConfigError("导航标识须为唯一的 1—40 位字母、数字、下划线或短横线。")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 10 or any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise PageConfigError("导航名称须为 1—10 个字符。")
        keys.add(key)
        if not isinstance(target, dict) or target.get("type") not in ("HOME", "MICRO", "CATEGORY", "CART", "ME"):
            raise PageConfigError("导航仅支持首页、微页面、分类、购物车和我的。")
        if target["type"] == "MICRO":
            if set(target) != {"type", "pageId"} or not target["pageId"]:
                raise PageConfigError("请选择关联微页面。")
            target = {"type": "MICRO", "pageId": _asset_id(target["pageId"])}
        elif set(target) != {"type"}:
            raise PageConfigError("固定导航目标不得包含其他链接参数。")
        icon, selected = _asset_id(item["iconAssetId"]), _asset_id(item["selectedIconAssetId"])
        if bool(icon) != bool(selected):
            raise PageConfigError("导航图标和选中图标须成对配置。")
        items.append({"key": key, "label": label.strip(), "target": target,
                      "iconAssetId": icon, "selectedIconAssetId": selected})
    return {"schemaVersion": 2, "items": items}


def validate_navigation_targets(config, *, publishing=False):
    if config.get("schemaVersion") != 2:
        return
    from .models import MicroPage, PagePublication
    identifiers = {item["target"]["pageId"] for item in config["items"] if item["target"]["type"] == "MICRO"}
    existing = set(map(str, MicroPage.objects.filter(pk__in=identifiers).values_list("id", flat=True)))
    if existing != identifiers:
        raise PageConfigError("导航关联页面不存在。", "NAVIGATION_TARGET_INVALID", 422)
    if publishing:
        publications = list(PagePublication.objects.filter(page_id__in=identifiers,
            current_version__isnull=False).select_related("page", "current_version"))
        published = {str(item.page_id) for item in publications}
        if published != identifiers:
            raise PageConfigError("导航关联页面尚未发布，请先发布页面。", "NAVIGATION_TARGET_UNPUBLISHED", 422)
        from .validation import validate_config
        from .runtime import check_runtime
        for publication in publications:
            config_json = publication.current_version.config_json
            validate_config(config_json, publishing=True, page_type=publication.page.page_type,
                            page_id=publication.page_id)
            check_runtime(config_json)


def defaults(domain):
    if domain == "navigation":
        return {"items": [{"key": key, "label": label, "iconAssetId": None,
                           "selectedIconAssetId": None} for key, label in NAVIGATION]}
    return {"enabled": False, "mode": None, "phone": None, "qrAssetId": None,
            "prompt": "", "iconAssetId": None}


def _asset_id(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise PageConfigError("素材 ID 必须是 UUID。", "MEDIA_INVALID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise PageConfigError("素材 ID 必须是 UUID。", "MEDIA_INVALID") from exc


def normalize(domain, value):
    if not isinstance(value, dict):
        raise PageConfigError("配置须为对象。")
    if domain == "navigation":
        if "schemaVersion" in value:
            return navigation_v2(value)
        if set(value) != {"items"} or not isinstance(value["items"], list) or len(value["items"]) != 4:
            raise PageConfigError("底部导航须保留四个固定入口。")
        items = []
        for item, (key, label) in zip(value["items"], NAVIGATION):
            if not isinstance(item, dict) or set(item) != {"key", "label", "iconAssetId", "selectedIconAssetId"}:
                raise PageConfigError("导航项参数不正确。")
            if item["key"] != key or item["label"] != label:
                raise PageConfigError("导航名称、顺序和目标页不可修改。")
            icon, selected = _asset_id(item["iconAssetId"]), _asset_id(item["selectedIconAssetId"])
            if bool(icon) != bool(selected):
                raise PageConfigError("导航图标和选中图标须成对配置。")
            items.append({"key": key, "label": label, "iconAssetId": icon,
                          "selectedIconAssetId": selected})
        return {"items": items}
    if domain == "customer_service":
        if set(value) != {"enabled", "mode", "phone", "qrAssetId", "prompt", "iconAssetId"}:
            raise PageConfigError("客服配置参数不正确。")
        enabled, mode, phone, prompt = (value[field] for field in ("enabled", "mode", "phone", "prompt"))
        if type(enabled) is not bool or mode not in (None, "PHONE", "QR") or \
                (phone is not None and not isinstance(phone, str)) or \
                not isinstance(prompt, str) or len(prompt.strip()) > 20 or \
                any(ord(character) < 32 or ord(character) == 127 for character in prompt):
            raise PageConfigError("客服配置字段不正确。")
        qr_id, icon_id = _asset_id(value["qrAssetId"]), _asset_id(value["iconAssetId"])
        if enabled and ((mode == "PHONE" and (not phone or not PHONE_PATTERN.fullmatch(phone) or qr_id)) or
                        (mode == "QR" and (not qr_id or phone)) or mode is None):
            raise PageConfigError("启用客服时须配置一种有效联系方式。", "CONTACT_INVALID", 422)
        if mode == "PHONE" and (qr_id or (phone and not PHONE_PATTERN.fullmatch(phone))):
            raise PageConfigError("电话客服配置不正确。", "CONTACT_INVALID")
        if mode == "QR" and phone:
            raise PageConfigError("二维码客服不得同时配置电话。", "CONTACT_INVALID")
        if mode is None and (phone or qr_id):
            raise PageConfigError("请先选择客服方式。", "CONTACT_INVALID")
        return {"enabled": enabled, "mode": mode, "phone": phone, "qrAssetId": qr_id,
                "prompt": prompt.strip(), "iconAssetId": icon_id}
    raise PageConfigError("配置域不存在。", "NOT_FOUND", 404)


def asset_ids(domain, config):
    if domain == "navigation":
        return {identifier for item in config["items"] for identifier in
                (item["iconAssetId"], item["selectedIconAssetId"]) if identifier}
    return {identifier for identifier in (config["qrAssetId"], config["iconAssetId"]) if identifier}


def validate_assets(domain, config, assets):
    ids = asset_ids(domain, config)
    if set(map(str, assets)) != ids:
        raise PageConfigError("素材不存在、已过期或文件丢失。", "MEDIA_INVALID", 422)
    if any(asset.kind != Asset.Kind.IMAGE or asset.content_type not in ("image/png", "image/jpeg")
           for asset in assets.values()):
        raise PageConfigError("导航与客服仅支持静态 PNG 或 JPEG 图片。", "MEDIA_INVALID", 422)
