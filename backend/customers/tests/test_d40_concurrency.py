from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import transaction, close_old_connections
from django.test import TransactionTestCase
from catalog.models import MemberGrade
from benefits.models import PointsPolicy
from benefits.policy import rules_data, update_rules_locked
from benefits.service import BenefitError

class RuleConcurrencyTests(TransactionTestCase):
    def setUp(self):
        MemberGrade.objects.get_or_create(code='normal',defaults={'name':'普通会员','rank':0})
        PointsPolicy.objects.get_or_create(pk=1)
    def test_same_revision_concurrent_edits_only_one_succeeds(self):
        initial=rules_data();barrier=Barrier(2)
        body={'expectedRevision':initial['revision'],'points':initial['points'],
              'grades':[{'id':g['id'],'minimumSpendFen':g['minimumSpendFen']} for g in initial['grades']],
              'reason':'Synthetic concurrency'}
        def run(earn):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    result=update_rules_locked({**body,'points':{**body['points'],'earnPoints':earn}})
                return result['revision']
            except BenefitError as exc:return exc.code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(run,2);b=pool.submit(run,3)
            results=[a.result(timeout=15),b.result(timeout=15)]
        self.assertCountEqual(results,[initial['revision']+1,'REVISION_CONFLICT'])
        self.assertEqual(PointsPolicy.objects.get(pk=1).revision,initial['revision']+1)
