"""Kdniao immediate-query boundary; caller owns cache and authorization."""
import base64
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ENDPOINT = "https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx"
MAX_RESPONSE = 64 * 1024


class TrackingUnavailable(Exception):
    """Provider/configuration failure with no private provider details."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _post(url, body, timeout):
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                      method="POST")
    with opener.open(request, timeout=timeout) as result:
        raw = result.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise TrackingUnavailable()
    return raw


def _event(row):
    if not isinstance(row, dict):
        raise TrackingUnavailable()
    moment, description = row.get("AcceptTime"), row.get("AcceptStation")
    if not isinstance(moment, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", moment):
        raise TrackingUnavailable()
    if not isinstance(description, str) or not 1 <= len(description) <= 240 or any(
            ord(char) < 32 or char in "<>" for char in description):
        raise TrackingUnavailable()
    return {"time": moment, "description": description}


def query_kdniao(carrier_code, tracking_no, business_id, app_key):
    if not (isinstance(carrier_code, str) and re.fullmatch(r"[A-Z0-9]{2,24}", carrier_code)
            and isinstance(tracking_no, str) and re.fullmatch(r"[!-~]{4,80}", tracking_no)
            and isinstance(business_id, str) and business_id.strip()
            and isinstance(app_key, str) and app_key.strip()):
        raise TrackingUnavailable()
    request_data = json.dumps({"ShipperCode": carrier_code, "LogisticCode": tracking_no},
                              ensure_ascii=False, separators=(",", ":"))
    signature = base64.b64encode(hashlib.md5((request_data + app_key).encode("utf-8")).digest()).decode("ascii")
    body = urlencode({"RequestData": request_data, "EBusinessID": business_id,
                      "RequestType": "1002", "DataSign": signature, "DataType": "2"}).encode("utf-8")
    try:
        raw = _post(ENDPOINT, body, 5)
        if len(raw) > MAX_RESPONSE:
            raise TrackingUnavailable()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("Success") is not True:
            raise TrackingUnavailable()
        rows = payload.get("Traces")
        if not isinstance(rows, list) or len(rows) > 200:
            raise TrackingUnavailable()
        events = [_event(row) for row in rows[-30:]]
        state = payload.get("State")
        if state not in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            raise TrackingUnavailable()
        status = "DELIVERED" if state == "3" else "EXCEPTION" if state in ("4", "6", "7") else \
            "IN_TRANSIT" if events else "NO_EVENTS"
        return {"status": status, "events": events}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError,
            TypeError, TrackingUnavailable) as exc:
        raise TrackingUnavailable() from exc
