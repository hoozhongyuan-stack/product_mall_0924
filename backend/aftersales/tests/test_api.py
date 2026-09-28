import uuid
import time
from django.test import TransactionTestCase, Client, override_settings
from django.db import transaction, DatabaseError
from customers.models import MemberSession, Member
from accounts.models import AdminAccount, PermissionGroup, GroupPermission
from payments.models import RefundEvidence
from fulfillment.tests import test_flow as flow


@override_settings(
    WECHAT_MINI_APP_ID="wx-payment-test",
    ORDER_PAYMENT_METHODS_ENABLED={"OFFLINE": True, "WECHAT": True},
)
class AfterSaleApiTests(TransactionTestCase):
    _order = flow.FulfillmentFlowTests._order
    _evidence = flow.FulfillmentFlowTests._evidence
    _paid_order = flow.FulfillmentFlowTests._paid_order

    def setUp(self):
        flow.FulfillmentFlowTests.setUp(self)
        self.order, self.line = self._paid_order()
        token, _ = MemberSession.issue(self.member)
        self.member_client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.admin = self.login(self.owner)
        self.checker = AdminAccount.objects.create_user(
            login_name="d1-checker",
            password="D1 synthetic checker!",
            display_name="复核员",
        )
        g = PermissionGroup.objects.create(code="d1-checker", name="复核员")
        for code in ["aftersale.read", "refund.offline.confirm"]:
            GroupPermission.objects.create(group=g, code=code)
        self.checker.permission_groups.add(g)
        self.check = self.login(self.checker)

    def login(self, actor):
        c = Client()
        c.force_login(actor)
        s = c.session
        s["admin_auth_version"] = actor.auth_version
        s["admin_last_active"] = time.time()
        s.save()
        return c

    def post(self, c, path, data, key=None, token=None):
        headers = {}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = str(key)
        if token:
            headers["HTTP_X_ACTION_CONFIRMATION"] = token
        return c.post(
            "/api/v1/" + path, data, content_type="application/json", **headers
        )

    def confirm(self, c, action, obj, rev, password):
        r = self.post(
            c,
            "admin/auth/confirm",
            {
                "action": action,
                "objectId": str(obj),
                "revision": rev,
                "password": password,
            },
        )
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["data"]["confirmationToken"]

    def apply(self):
        r = self.post(
            self.member_client,
            "app/aftersales",
            {
                "lineId": str(self.line.id),
                "kind": "REFUND_ONLY",
                "redemptionScope": "UNUSED",
                "quantity": 1,
                "reason": "合成售后申请原因",
            },
            uuid.uuid4(),
        )
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["data"]

    def approved(self):
        case = self.apply()
        token = self.confirm(
            self.admin,
            "aftersale.review",
            case["caseId"],
            1,
            "Long test password 2026!",
        )
        r = self.post(
            self.admin,
            f"admin/aftersales/{case['caseId']}/review",
            {"approve": True, "reason": "合成审核通过原因", "expectedRevision": 1},
            token=token,
        )
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["data"]

    def test_options_preview_apply_withdraw_and_privacy(self):
        r = self.member_client.get(
            f"/api/v1/app/orders/{self.order.id}/aftersale-options"
        )
        self.assertEqual(r.status_code, 200, r.content)
        p = self.post(
            self.member_client,
            "app/aftersales/preview",
            {
                "lineId": str(self.line.id),
                "kind": "REFUND_ONLY",
                "redemptionScope": "UNUSED",
                "quantity": 1,
            },
        )
        self.assertEqual(p.status_code, 200, p.content)
        case = self.apply()
        self.assertEqual(case["amountFen"], p.json()["data"]["amountFen"])
        self.assertNotIn("offlineRefund", case)
        d = self.member_client.get("/api/v1/app/aftersales/" + case["caseId"])
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d["Cache-Control"], "private, no-store")
        self.assertEqual(
            self.post(
                self.member_client,
                "app/aftersales/" + case["caseId"] + "/withdraw",
                {"expectedRevision": 1},
            ).json()["data"]["status"],
            "WITHDRAWN",
        )
        self.assertEqual(
            Client().get("/api/v1/app/aftersales/" + case["caseId"]).status_code, 401
        )

    def test_review_requires_scoped_confirmation(self):
        case = self.apply()
        path = "admin/aftersales/" + case["caseId"] + "/review"
        body = {"approve": False, "reason": "合成拒绝申请原因", "expectedRevision": 1}
        self.assertEqual(self.post(self.admin, path, body).status_code, 403)
        token = self.confirm(
            self.admin, "aftersale.review", uuid.uuid4(), 1, "Long test password 2026!"
        )
        self.assertEqual(
            self.post(self.admin, path, body, token=token).status_code, 403
        )

    def test_saved_facts_only_dual_authorization_refund_once(self):
        case = self.approved()
        path = "admin/aftersales/" + case["caseId"] + "/offline-refunds"
        body = {
            "expectedRevision": 2,
            "merchantAccountId": "offline-test-account",
            "externalRefundNo": "D1-SYNTHETIC-1",
            "amountFen": case["amountFen"],
            "refundedAt": __import__("django.utils.timezone", fromlist=["now"])
            .now()
            .isoformat(),
            "refundMethod": "BANK_TRANSFER",
            "proofReference": "SYNTHETIC-BANK-RECEIPT-1",
            "note": "合成流水已核对",
            "verified": True,
        }
        detail = self.admin.get("/api/v1/admin/aftersales/" + case["caseId"]).json()[
            "data"
        ]
        body["merchantAccountId"] = detail["refundSource"]["merchantAccountId"]
        key = uuid.uuid4()
        r = self.post(self.admin, path, body, key)
        self.assertEqual(r.status_code, 201, r.content)
        row = r.json()["data"]
        self.assertEqual(RefundEvidence.objects.count(), 0)
        self.assertEqual(self.post(self.admin, path, body, key).status_code, 200)
        cp = (
            "admin/refunds/offline-reconciliations/"
            + row["reconciliationId"]
            + "/confirm"
        )
        self.assertEqual(self.post(self.admin, cp, {}).status_code, 403)
        token = self.confirm(
            self.check,
            "refund.offline.confirm",
            row["reconciliationId"],
            1,
            "D1 synthetic checker!",
        )
        r = self.post(self.check, cp, {}, token=token)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["data"]["outcome"], "SUCCEEDED")
        self.assertEqual(self.post(self.check, cp, {}).status_code, 200)
        self.assertEqual(RefundEvidence.objects.count(), 1)
        from payments.models import OfflineRefundReconciliation

        with self.assertRaises(DatabaseError), transaction.atomic():
            OfflineRefundReconciliation.objects.filter(
                pk=row["reconciliationId"]
            ).update(amount_fen=1)

    def test_foreign_member_cannot_read_or_withdraw_and_disabled_member_rejected(self):
        case = self.apply()
        stranger = Member.objects.create(
            wechat_app_id=self.member.wechat_app_id,
            wechat_openid="d1-stranger",
            grade=self.member.grade,
        )
        token, _ = MemberSession.issue(stranger)
        c = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(
            c.get("/api/v1/app/aftersales/" + case["caseId"]).status_code, 404
        )
        self.assertEqual(
            self.post(
                c,
                "app/aftersales/" + case["caseId"] + "/withdraw",
                {"expectedRevision": 1},
            ).status_code,
            404,
        )
        from aftersales.service import withdraw_case, AfterSaleError

        Member.objects.filter(pk=self.member.pk).update(enabled=False)
        with self.assertRaises(AfterSaleError):
            withdraw_case(case["caseId"], self.member, 1)

    def test_admin_mutation_has_csrf_and_readonly_does_not_see_money(self):
        case = self.apply()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        s = c.session
        s["admin_auth_version"] = self.owner.auth_version
        s["admin_last_active"] = time.time()
        s.save()
        self.assertEqual(
            self.post(
                c,
                "admin/aftersales/" + case["caseId"] + "/review",
                {"approve": False, "reason": "合成拒绝原因", "expectedRevision": 1},
            ).status_code,
            403,
        )
        read = AdminAccount.objects.create_user(
            login_name="readonly", password="D1 readonly password!", display_name="只读"
        )
        group = PermissionGroup.objects.create(code="d1-readonly", name="只读")
        GroupPermission.objects.create(group=group, code="aftersale.read")
        read.permission_groups.add(group)
        detail = (
            self.login(read)
            .get("/api/v1/admin/aftersales/" + case["caseId"])
            .json()["data"]
        )
        self.assertNotIn("refundSource", detail)
        self.assertNotIn("offlineRefund", detail)
        self.assertEqual(
            self.post(
                self.login(read), "admin/aftersales/" + case["caseId"] + "/review", {}
            ).status_code,
            403,
        )

    def test_refund_validation_and_failed_settlement_recovery_without_new_transfer(
        self,
    ):
        case = self.approved()
        detail = self.admin.get("/api/v1/admin/aftersales/" + case["caseId"]).json()[
            "data"
        ]
        from django.utils import timezone

        body = {
            "expectedRevision": 2,
            "merchantAccountId": detail["refundSource"]["merchantAccountId"],
            "externalRefundNo": "D1-RECOVERY",
            "amountFen": case["amountFen"],
            "refundedAt": timezone.now().isoformat(),
            "refundMethod": "BANK_TRANSFER",
            "proofReference": "BANK-ARCHIVE-RECOVERY",
            "note": "",
            "verified": True,
        }
        path = "admin/aftersales/" + case["caseId"] + "/offline-refunds"
        bad = {**body, "amountFen": body["amountFen"] + 1}
        self.assertEqual(
            self.post(self.admin, path, bad, uuid.uuid4()).status_code, 409
        )
        bad = {**body, "proofReference": "https://untrusted.example/receipt"}
        self.assertEqual(
            self.post(self.admin, path, bad, uuid.uuid4()).status_code, 400
        )
        r = self.post(self.admin, path, body, uuid.uuid4())
        self.assertEqual(r.status_code, 201, r.content)
        row = r.json()["data"]
        cp = (
            "admin/refunds/offline-reconciliations/"
            + row["reconciliationId"]
            + "/confirm"
        )
        token = self.confirm(
            self.check,
            "refund.offline.confirm",
            row["reconciliationId"],
            1,
            "D1 synthetic checker!",
        )
        from unittest.mock import patch

        with patch(
            "payments.refunds._finalize_case",
            side_effect=RuntimeError("synthetic local fault"),
        ):
            r = self.post(self.check, cp, {}, token=token)
        self.assertEqual(r.json()["data"]["outcome"], "SETTLEMENT_FAILED")
        self.assertEqual(RefundEvidence.objects.count(), 1)
        from payments.refunds import recover_recorded_refunds

        self.assertEqual(recover_recorded_refunds()["succeeded"], 1)
        detail = self.check.get("/api/v1/admin/aftersales/" + case["caseId"]).json()[
            "data"
        ]
        self.assertEqual(detail["offlineRefund"]["outcome"], "SUCCEEDED")
        self.assertFalse(detail["offlineRefund"]["canConfirm"])
        self.assertEqual(self.post(self.check, cp, {}).status_code, 200)
        self.assertEqual(RefundEvidence.objects.count(), 1)

    def test_application_idempotency_validation_and_pagination(self):
        body = {
            "lineId": str(self.line.id),
            "kind": "REFUND_ONLY",
            "redemptionScope": "UNUSED",
            "quantity": 1,
            "reason": "合成重复申请原因",
        }
        key = uuid.uuid4()
        first = self.post(self.member_client, "app/aftersales", body, key)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(
            self.post(self.member_client, "app/aftersales", body, key).status_code, 200
        )
        self.assertEqual(
            self.post(
                self.member_client, "app/aftersales", {**body, "quantity": 2}, key
            ).status_code,
            409,
        )
        self.assertEqual(
            self.member_client.get("/api/v1/app/aftersales?pageSize=101").status_code,
            400,
        )
        self.assertEqual(
            self.member_client.get(
                "/api/v1/app/aftersales?orderId=" + str(self.order.id)
            ).json()["data"]["total"],
            1,
        )
        self.assertEqual(
            self.post(
                self.member_client,
                "app/aftersales/preview",
                {
                    "lineId": str(self.line.id),
                    "kind": "REFUND_ONLY",
                    "redemptionScope": "UNUSED",
                    "quantity": True,
                },
            ).status_code,
            400,
        )

    def test_review_confirmation_revision_session_and_revoked_permissions(self):
        case = self.apply()
        path = "admin/aftersales/" + case["caseId"] + "/review"
        body = {"approve": True, "reason": "合成授权审核原因", "expectedRevision": 1}
        token = self.confirm(
            self.admin,
            "aftersale.review",
            case["caseId"],
            2,
            "Long test password 2026!",
        )
        self.assertEqual(
            self.post(self.admin, path, body, token=token).status_code, 403
        )
        token = self.confirm(
            self.admin,
            "aftersale.review",
            case["caseId"],
            1,
            "Long test password 2026!",
        )
        self.assertEqual(
            self.post(self.login(self.owner), path, body, token=token).status_code, 403
        )
        reviewer = AdminAccount.objects.create_user(
            login_name="revoked-reviewer",
            password="D1 reviewer password!",
            display_name="审核员",
        )
        g = PermissionGroup.objects.create(code="d1-reviewer", name="审核员")
        for code in ("aftersale.read", "aftersale.review"):
            GroupPermission.objects.create(group=g, code=code)
        reviewer.permission_groups.add(g)
        c = self.login(reviewer)
        token = self.confirm(
            c, "aftersale.review", case["caseId"], 1, "D1 reviewer password!"
        )
        GroupPermission.objects.filter(group=g, code="aftersale.review").delete()
        self.assertEqual(self.post(c, path, body, token=token).status_code, 403)

    def test_queue_filter_and_bearer_only_member_mutations(self):
        case = self.apply()
        listing = self.admin.get(
            "/api/v1/admin/aftersales?status=PENDING_REVIEW&orderNo="
            + self.order.order_no
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["total"], 1)
        self.assertEqual(
            self.admin.get("/api/v1/admin/aftersales?status=UNKNOWN").status_code, 400
        )
        self.assertEqual(
            self.admin.get("/api/v1/admin/aftersales?orderId=invalid").status_code, 400
        )
        for path in (
            "app/aftersales",
            "app/aftersales/preview",
            "app/aftersales/" + case["caseId"] + "/withdraw",
        ):
            self.assertEqual(self.post(self.admin, path, {}).status_code, 401)
        self.assertEqual(
            self.post(
                self.member_client,
                "app/aftersales/preview",
                {
                    "lineId": "invalid",
                    "kind": "REFUND_ONLY",
                    "redemptionScope": "UNUSED",
                    "quantity": 1,
                },
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(self.member_client, "app/aftersales", {}).status_code, 400
        )
        self.assertEqual(
            self.admin.get("/api/v1/admin/aftersales/" + str(uuid.uuid4())).status_code,
            404,
        )

    def test_return_refund_waits_for_acceptance_and_cannot_register_funds(self):
        order, line = self._paid_order("SHIP")
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order

        Carrier.objects.create(code="D1SF", name="合成承运商", enabled=True)
        ship_order(
            order.id, self.owner, "D1SF", "D1SF-SYNTHETIC", order.revision, uuid.uuid4()
        )
        options = self.member_client.get(
            "/api/v1/app/orders/" + str(order.id) + "/aftersale-options"
        ).json()["data"]
        self.assertEqual(options["items"][0]["options"][0]["kind"], "RETURN_REFUND")
        r = self.post(
            self.member_client,
            "app/aftersales",
            {
                "lineId": str(line.id),
                "kind": "RETURN_REFUND",
                "redemptionScope": "UNUSED",
                "quantity": 1,
                "reason": "合成退货申请原因",
            },
            uuid.uuid4(),
        )
        self.assertEqual(r.status_code, 201, r.content)
        case = r.json()["data"]
        token = self.confirm(
            self.admin,
            "aftersale.review",
            case["caseId"],
            1,
            "Long test password 2026!",
        )
        r = self.post(
            self.admin,
            "admin/aftersales/" + case["caseId"] + "/review",
            {"approve": True, "reason": "合成退货审核原因", "expectedRevision": 1},
            token=token,
        )
        self.assertEqual(r.json()["data"]["status"], "WAITING_RETURN")
        from django.utils import timezone

        body = {
            "expectedRevision": 2,
            "merchantAccountId": "test-account",
            "externalRefundNo": "D1-NOT-ACCEPTED",
            "amountFen": case["amountFen"],
            "refundedAt": timezone.now().isoformat(),
            "refundMethod": "BANK_TRANSFER",
            "proofReference": "D1-BANK-ARCHIVE",
            "note": "",
            "verified": True,
        }
        self.assertEqual(
            self.post(
                self.admin,
                "admin/aftersales/" + case["caseId"] + "/offline-refunds",
                body,
                uuid.uuid4(),
            ).status_code,
            409,
        )
