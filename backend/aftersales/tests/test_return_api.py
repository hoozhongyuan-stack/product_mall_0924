"""D2 HTTP permissions, scoped password confirmation, privacy, and race boundaries."""
import uuid
from django.test import Client,TransactionTestCase,override_settings
from . import test_returns as return_fixture
from . import test_api as api_fixture

@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", ORDER_PAYMENT_METHODS_ENABLED={"WECHAT":True,"OFFLINE":True})
class ReturnApiTests(TransactionTestCase):
    setUp=return_fixture.ReturnFlowTests.setUp
    _order=return_fixture.ReturnFlowTests._order
    _evidence=return_fixture.ReturnFlowTests._evidence
    _paid_order=return_fixture.ReturnFlowTests._paid_order
    _case=return_fixture.ReturnFlowTests._case
    _body=return_fixture.ReturnFlowTests._body
    login=api_fixture.AfterSaleApiTests.login
    post=api_fixture.AfterSaleApiTests.post
    confirm=api_fixture.AfterSaleApiTests.confirm

    def test_return_logistics_and_acceptance_api(self):
        from customers.models import MemberSession
        c=self._case(); token,_=MemberSession.issue(self.member)
        member=Client(HTTP_AUTHORIZATION="Bearer "+token)
        admin=self.login(self.owner)
        logistics={"expectedRevision":c.revision,"carrierName":"合成承运商","trackingNo":"D2-RET-API"}
        key=uuid.uuid4(); path=f"app/aftersales/{c.id}/return-shipment"
        r=self.post(member,path,logistics,key); self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(self.post(member,path,logistics,key).status_code,200)
        self.assertEqual(self.post(admin,path,logistics,key).status_code,401)
        c.refresh_from_db(); body=self._body(c)
        preview=f"admin/aftersales/{c.id}/return-acceptance/preview"
        r=self.post(admin,preview,body); self.assertEqual(r.status_code,200,r.content)
        path=f"admin/aftersales/{c.id}/return-acceptance"; key=uuid.uuid4()
        self.assertEqual(self.post(admin,path,body,key).status_code,403)
        confirmation=self.confirm(admin,"aftersale.return.accept",c.id,c.revision,"Long test password 2026!")
        r=self.post(admin,path,body,key,confirmation); self.assertEqual(r.status_code,200,r.content)
        dto=r.json()["data"]; self.assertEqual(dto["status"],"WAITING_REFUND")
        self.assertEqual(dto["refundSource"]["amountFen"],dto["effectiveRefundAmountFen"])
        self.assertEqual(self.post(admin,path,body,key).status_code,200)
        d=member.get(f"/api/v1/app/aftersales/{c.id}").json()["data"]
        self.assertNotIn("actorName",d["returnAcceptance"]); self.assertNotIn("refundSource",d)
        self.assertFalse(d["canSubmitReturnShipment"])

    def test_foreign_member_and_csrf_rejected(self):
        c=self._case(); from customers.models import Member,MemberSession
        other=Member.objects.create(wechat_app_id=self.member.wechat_app_id,wechat_openid="d2-other",grade=self.member.grade)
        token,_=MemberSession.issue(other); m=Client(HTTP_AUTHORIZATION="Bearer "+token)
        b={"expectedRevision":c.revision,"carrierName":"合成承运商","trackingNo":"D2-OTHER"}
        self.assertEqual(self.post(m,f"app/aftersales/{c.id}/return-shipment",b,uuid.uuid4()).status_code,404)
        admin=Client(enforce_csrf_checks=True); admin.force_login(self.owner)
        self.assertEqual(self.post(admin,f"admin/aftersales/{c.id}/return-acceptance",self._body(c),uuid.uuid4()).status_code,403)

    def test_stale_logistics_revision_cannot_accept(self):
        from aftersales.returns import submit_return_shipment
        c=self._case(); body=self._body(c)
        submit_return_shipment(c.id,self.member,{"expectedRevision":c.revision,"carrierName":"合成承运商","trackingNo":"UPDATED"},uuid.uuid4())
        admin=self.login(self.owner)
        r=self.post(admin,f"admin/aftersales/{c.id}/return-acceptance/preview",body)
        self.assertEqual(r.status_code,409)
