import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.test import TransactionTestCase
from django.db import connections, close_old_connections
from stores.procurement.service import transition
from stores.procurement.models import ProcurementOrder
from stores.access import StoreError
from inventory.models import InventoryBalance, InventoryLedger
from .test_procurement import ProcurementFixture


class ProcurementConcurrencyTests(ProcurementFixture,TransactionTestCase):
    def setUp(self):self.setup_fixture()

    def race(self,same_key):
        row=self.shipped()
        gate=Barrier(2)
        line_ids=[str(x.pk) for x in row.lines.all()]
        common=str(uuid.uuid4())
        def receive(index):
            close_old_connections()
            try:
                current=ProcurementOrder.objects.get(pk=row.pk)
                body={'expectedRevision':row.revision,'requestKey':common if same_key else str(uuid.uuid4()),'lineIds':line_ids}
                gate.wait(timeout=10)
                try:return transition(current,self.member,'receive',body).status
                except StoreError as exc:return exc.code
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(receive,[0,1]))
        self.assertEqual(InventoryBalance.objects.get(warehouse=self.store.warehouse).on_hand_base_units,2)
        self.assertEqual(InventoryLedger.objects.filter(movement_type='PROCURE').count(),1)
        return results

    def test_same_request_key_parallel_receipt_is_exactly_once(self):
        self.assertEqual(self.race(True),['RECEIVED','RECEIVED'])

    def test_distinct_parallel_receipts_conflict_and_stock_is_once(self):
        self.assertCountEqual(self.race(False),['RECEIVED','REVISION_CONFLICT'])
