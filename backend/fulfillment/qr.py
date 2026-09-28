"""Render a member's short redemption bearer code as a scannable PNG."""
import base64
from io import BytesIO

import segno

from .codes import CODE_RE


def voucher_qr_data_url(code):
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise ValueError("核销凭证码格式不正确。")
    image = BytesIO()
    segno.make_qr(code, error="m").save(
        image, kind="png", scale=6, border=4, dark="#000000", light="#ffffff")
    return "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
