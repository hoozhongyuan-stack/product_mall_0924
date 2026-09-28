"""E4 cash operations read model from final local settlement facts."""

from datetime import timedelta

from django.db import connection
from django.views.decorators.cache import never_cache

from common.http import method
from accounts.read_window import read_window
from accounts.read_rate_limit import read_rate_limit
from accounts.security import error, require, response


SQL = """
SELECT day, SUM(paid_count), SUM(paid_fen), SUM(points_count), SUM(refund_count), SUM(refund_fen)
FROM (
  SELECT (paid_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
         COUNT(*) AS paid_count, COALESCE(SUM(payable_fen), 0) AS paid_fen,
         0 AS points_count, 0 AS refund_count, 0 AS refund_fen
  FROM customer_order
  WHERE status = 'PAID' AND order_kind = 'CASH' AND paid_at >= %s AND paid_at < %s
  GROUP BY 1
  UNION ALL
  SELECT (paid_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
         0, 0, COUNT(*), 0, 0
  FROM customer_order
  WHERE status = 'PAID' AND order_kind = 'POINTS' AND paid_at >= %s AND paid_at < %s
  GROUP BY 1
  UNION ALL
  SELECT (succeeded_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
         0, 0, 0, COUNT(*), COALESCE(SUM(amount_fen), 0)
  FROM refund_intent
  WHERE status = 'SUCCEEDED' AND succeeded_at >= %s AND succeeded_at < %s
  GROUP BY 1
) AS facts
GROUP BY day ORDER BY day
"""


def business_summary(first, last, start, end):
    """Use one SQL snapshot for the API and the asynchronous CSV producer."""
    with connection.cursor() as cursor:
        cursor.execute(SQL, [start, end, start, end, start, end])
        facts = {row[0]: tuple(int(value) for value in row[1:]) for row in cursor.fetchall()}
    totals = {"paidOrderCount": 0, "paidAmountFen": 0, "pointsExchangeCount": 0,
              "refundCount": 0, "refundAmountFen": 0, "netAmountFen": 0}
    days = []
    current = first
    while current <= last:
        paid_count, paid_fen, points_count, refund_count, refund_fen = facts.get(current, (0, 0, 0, 0, 0))
        item = {"date": current.isoformat(), "paidOrderCount": paid_count, "paidAmountFen": paid_fen,
                "pointsExchangeCount": points_count, "refundCount": refund_count,
                "refundAmountFen": refund_fen, "netAmountFen": paid_fen - refund_fen}
        days.append(item)
        for field in totals:
            totals[field] += item[field]
        current += timedelta(days=1)
    return {"from": first.isoformat(), "to": last.isoformat(),
            "timeZone": "Asia/Shanghai", "basis": {"payment": "Order.paid_at",
            "refund": "RefundIntent.succeeded_at"}, "totals": totals, "days": days}


@never_cache
def summary_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    actor, bad = require(request, "business.report.read")
    if bad:
        return bad
    try:
        if set(request.GET) - {"from", "to"} or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
            raise ValueError("筛选参数不正确。")
        first, last, start, end = read_window(request.GET)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    limited = read_rate_limit(request, actor, "business")
    if limited:
        return limited
    return response(request, business_summary(first, last, start, end))
