import html
import re
import uuid
from datetime import date
from html.parser import HTMLParser


CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ALLOWED_TAGS = frozenset({"p", "br", "strong", "em", "ul", "ol", "li", "h2", "h3"})


class CatalogError(Exception):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def redeem_valid_until(value, fulfillment_kind):
    """A visible fixed calendar date, never an implied hidden validity period."""
    if fulfillment_kind == "SHIP":
        if value not in (None, ""):
            raise CatalogError("快递商品不能设置核销有效期。")
        return None
    if value in (None, ""):
        return None  # Drafts may be incomplete, but cannot become on sale.
    if not isinstance(value, str) or len(value) != 10:
        raise CatalogError("核销有效期须为 YYYY-MM-DD 日期。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise CatalogError("核销有效期须为有效日期。") from None
    if parsed.isoformat() != value:
        raise CatalogError("核销有效期须为 YYYY-MM-DD 日期。")
    return parsed


class DescriptionSanitizer(HTMLParser):
    def __init__(self, image_url=None):
        super().__init__(convert_charrefs=True)
        self.image_url = image_url
        self.image_ids = []
        self.parts = []
        self.open_tags = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.skip_depth += 1
        elif not self.skip_depth and tag == "img":
            self.handle_image(attrs)
        elif not self.skip_depth and tag in ALLOWED_TAGS:
            self.parts.append(f"<{tag}>")
            if tag != "br":
                self.open_tags.append(tag)

    def handle_image(self, attrs):
        identifiers = [value for key, value in attrs if key == "data-asset-id"]
        alts = [value for key, value in attrs if key == "alt"]
        if len(identifiers) != 1 or not identifiers[0]:
            raise CatalogError("详情图片必须绑定有效素材。", "MEDIA_INVALID")
        try:
            identifier = uuid.UUID(identifiers[0])
        except ValueError:
            raise CatalogError("详情图片素材 ID 无效。", "MEDIA_INVALID") from None
        if len(alts) > 1 or alts and (alts[0] is None or len(alts[0]) > 200):
            raise CatalogError("图片说明不能超过 200 字符且不能重复。")
        if len(self.image_ids) >= 20:
            raise CatalogError("详情图片不能超过 20 张。")
        self.image_ids.append(identifier)
        attributes = f' data-asset-id="{identifier}"'
        if alts:
            attributes += f' alt="{html.escape(alts[0], quote=True)}"'
        if self.image_url:
            attributes += f' src="{html.escape(self.image_url(identifier), quote=True)}"'
        self.parts.append(f"<img{attributes}>")

    def handle_startendtag(self, tag, attrs):
        if not self.skip_depth and tag == "img":
            self.handle_image(attrs)
        if not self.skip_depth and tag == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.skip_depth = max(self.skip_depth - 1, 0)
        elif not self.skip_depth and tag in self.open_tags:
            while self.open_tags:
                current = self.open_tags.pop()
                self.parts.append(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(html.escape(data, quote=True))

    def value(self):
        return "".join([*self.parts, *(f"</{tag}>" for tag in reversed(self.open_tags))])


def description_parser(value):
    # HTTP has a 256 KiB body limit; direct service calls must also remain bounded.
    if not isinstance(value, str) or len(value) > 262144:
        raise CatalogError("详情内容过长或格式无效。")
    parser = DescriptionSanitizer()
    parser.feed(value)
    parser.close()
    if len(parser.value()) > 20000:
        raise CatalogError("详情内容不能超过 20000 字符。")
    return parser


def description(value):
    return description_parser(value).value()


def text_field(value, label, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise CatalogError(f"{label}长度必须在 1—{maximum} 字符之间。")
    return value.strip()


def code_field(value, label):
    if not isinstance(value, str) or not CODE_PATTERN.fullmatch(value):
        raise CatalogError(f"{label}只接受 1—64 位英文字母、数字、- 和 _。")
    return value


def number_field(value, label, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CatalogError(f"{label}必须是 {minimum} 到 {maximum} 之间的整数。")
    return value


def uuid_field(value, label):
    if not isinstance(value, str):
        raise CatalogError(f"{label}格式不正确。")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise CatalogError(f"{label}格式不正确。") from exc


def object_field(value, label):
    if not isinstance(value, dict):
        raise CatalogError(f"{label}必须是对象。")
    return value


def list_field(value, label, maximum, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise CatalogError(f"{label}数量必须在 {minimum}—{maximum} 之间。")
    return value
