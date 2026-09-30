"""Shared-pool evidence across sale, shipment and return in PostgreSQL."""

import uuid

from django.test import TransactionTestCase, override_settings

from accounts.models import AdminAccount
from aftersales.returns import accept_return
from aftersales.tests import test_returns as return_fixture
from catalog.models import Sku, SkuUnitVersion
from inventory.models import InventoryBalance, InventoryLedger
from inventory.pool_access import bind_sku_to_pool, ensure_independent_pool
from payments.tests import test_payment_flow as payment_fixture


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class SharedPoolReturnTests(TransactionTestCase):
    _order = return_fixture.ReturnFlowTests._order
    _evidence = return_fixture.ReturnFlowTests._evidence
    _paid_order = return_fixture.ReturnFlowTests._paid_order
    _case = return_fixture.ReturnFlowTests._case
    _body = return_fixture.ReturnFlowTests._body

    def setUp(self):
        payment_fixture.PaymentFlowTests.setUp(self)
        self.owner = AdminAccount.objects.get(login_name="payment-owner")

    def _alias(self):
        anchor = self.sku
        pool = ensure_independent_pool(anchor)
        alias = Sku.objects.create(product=anchor.product, sku_code="POOL-RETURN-ALIAS",
                                   spec_key="pool-return-alias", list_price_fen=anchor.list_price_fen,
                                   sale_status="ON_SALE")
        unit = SkuUnitVersion.objects.create(sku=alias, base_unit=anchor.current_unit.base_unit,
                                             sale_unit=anchor.current_unit.sale_unit,
                                             ratio=anchor.current_unit.ratio)
        alias.current_unit = unit
        alias.save(update_fields=["current_unit"])
        bind_sku_to_pool(alias.id, pool.id, alias.revision)
        return anchor, alias

    def test_alias_sale_and_accepted_return_restore_anchor_balance(self):
        anchor, alias = self._alias()
        self.sku = alias
        case = self._case()
        balance = InventoryBalance.objects.get(sku=anchor)
        before = balance.on_hand_base_units
        sale = InventoryLedger.objects.get(order_line=case.order_line)
        self.assertEqual(sale.sku_id, alias.id)
        accept_return(case.id, self.owner, self._body(case), uuid.uuid4())
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_base_units, before + case.order_line.ratio)
        self.assertEqual(InventoryLedger.objects.get(return_case_id=case.id).sku_id, alias.id)

    @override_settings(EXCHANGE_ORDER_ENABLED=True)
    def test_alias_points_exchange_consumes_anchor_balance(self):
        from datetime import timedelta

        from django.utils import timezone

        from benefits.service import grant_points
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote, submit_exchange_order

        anchor, alias = self._alias()
        grant_points(self.member, 1000, timezone.now() + timedelta(days=5), "pool-points")
        offer = ExchangeOffer.objects.create(sku=alias, points_price=100, status="ON_SALE")
        quote = create_exchange_quote(self.member, {"offerId": str(offer.id), "quantity": 2})
        result, replay = submit_exchange_order(self.member, {"quoteId": quote["quoteId"]}, uuid.uuid4())
        self.assertFalse(replay)
        self.assertEqual(result["status"], "PAID")
        balance = InventoryBalance.objects.get(sku=anchor)
        self.assertEqual(balance.on_hand_base_units, 8)
        self.assertEqual(InventoryLedger.objects.get(order_line__order_id=result["orderId"]).sku_id,
                         alias.id)
