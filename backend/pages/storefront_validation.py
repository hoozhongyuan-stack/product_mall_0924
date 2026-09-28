"""Strict, closed configuration contract for the two storefront singletons."""
import re
import uuid

from catalog.models import Asset
from .validation import PageConfigError


NAVIGATION = (("HOME", "首页"), ("CATEGORY", "分类"),
              ("CART", "购物车"), ("ME", "我的"))
PHONE_PATTERN = re.compile(r"^1[3-9][0-9]{9}$")


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
