"""Permit a sold SKU to consume the balance of its physical pool anchor."""

import importlib
import re

from django.db import migrations


def previous_function(module, source, name):
    sql = getattr(importlib.import_module(module), source)
    pattern = rf"CREATE (?:OR REPLACE )?FUNCTION {name}\(\).*?\$\$ LANGUAGE plpgsql;"
    match = re.search(pattern, sql, re.S)
    if match is None:
        pattern = rf"CREATE (?:OR REPLACE )?FUNCTION {name}\(\).*?END \$\$;"
        match = re.search(pattern, sql, re.S)
    if match is None:
        raise RuntimeError(f"Could not locate {name} in {module}")
    return match.group(0).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


ANCHOR = "COALESCE((SELECT p.anchor_sku_id FROM inventory_stock_pool_sku m JOIN inventory_stock_pool p ON p.id=m.pool_id WHERE m.sku_id=l.sku_id), l.sku_id)"
ORDER_OLD = previous_function("orders.migrations.0013_exchange_immutable_event_proofs", "FORWARD",
                              "order_points_total_guard")
RETURN_OLD = previous_function("aftersales.migrations.0005_return_guards", "SQL",
                               "check_return_effects")

ORDER_NEW = ORDER_OLD.replace("b.sku_id=l.sku_id", f"b.sku_id={ANCHOR}")
RETURN_NEW = RETURN_OLD.replace("b.sku_id=l.sku_id", f"b.sku_id={ANCHOR}")
if ORDER_NEW == ORDER_OLD or RETURN_NEW == RETURN_OLD:
    raise RuntimeError("Pool evidence migration did not find old anchor comparisons")


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0011_stock_pool"),
        ("orders", "0013_exchange_immutable_event_proofs"),
        ("aftersales", "0007_d3_settlement_guards"),
    ]
    operations = [migrations.RunSQL(ORDER_NEW + RETURN_NEW, ORDER_OLD + RETURN_OLD)]
