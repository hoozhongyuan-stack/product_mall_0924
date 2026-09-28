import json

from django.test import Client, TestCase

from accounts.models import AdminAccount, AuditLog, PermissionGroup, GroupPermission


class ShippingPolicyTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "shipping-owner", "Safe owner passphrase 2026!", display_name="主账号", kind="OWNER")
        self.client = Client(enforce_csrf_checks=True)
        self.client.get("/api/v1/admin/auth/csrf")
        self._login(self.client, "shipping-owner", "Safe owner passphrase 2026!")

    def _login(self, client, name, password):
        return client.post("/api/v1/admin/auth/login",
                           data=json.dumps({"loginName": name, "password": password}),
                           content_type="application/json",
                           HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def _put(self, client, body):
        return client.put("/api/v1/admin/settlement/shipping-policy",
                          data=json.dumps(body), content_type="application/json",
                          HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def test_default_and_revisioned_update_with_audit(self):
        from shipping.models import ShippingPolicy

        initial = self.client.get("/api/v1/admin/settlement/shipping-policy")
        self.assertEqual(initial.status_code, 200, initial.content)
        self.assertEqual(initial.json()["data"], {
            "feeFen": 1000, "deliveryScope": "NATIONWIDE", "revision": 1,
        })
        saved = self._put(self.client, {"feeFen": 1200, "deliveryScope": "NATIONWIDE",
                                        "expectedRevision": 1})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["data"]["revision"], 2)
        self.assertEqual(ShippingPolicy.objects.get(pk=1).fee_fen, 1200)
        self.assertTrue(AuditLog.objects.filter(action_code="shipping.policy.update", actor=self.owner).exists())
        stale = self._put(self.client, {"feeFen": 800, "deliveryScope": "NATIONWIDE",
                                        "expectedRevision": 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(ShippingPolicy.objects.get(pk=1).fee_fen, 1200)

    def test_same_policy_save_does_not_invalidate_existing_quotes(self):
        unchanged = self._put(self.client, {"feeFen": 1000, "deliveryScope": "NATIONWIDE",
                                            "expectedRevision": 1})
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertEqual(unchanged.json()["data"]["revision"], 1)
        self.assertFalse(AuditLog.objects.filter(action_code="shipping.policy.update").exists())

    def test_invalid_fee_and_scope_are_rejected(self):
        for fee in (-1, 1.5, "1000", 1_000_001):
            self.assertEqual(self._put(self.client, {"feeFen": fee, "deliveryScope": "NATIONWIDE",
                                                     "expectedRevision": 1}).status_code, 400)
        self.assertEqual(self._put(self.client, {"feeFen": 1000, "deliveryScope": "LOCAL",
                                                 "expectedRevision": 1}).status_code, 400)

    def test_write_requires_csrf_token(self):
        response = self.client.put("/api/v1/admin/settlement/shipping-policy",
                                   data=json.dumps({"feeFen": 900, "deliveryScope": "NATIONWIDE",
                                                    "expectedRevision": 1}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_staff_without_permission_cannot_read_or_change(self):
        group = PermissionGroup.objects.create(code="shipping-reader", name="无运费权限")
        GroupPermission.objects.create(group=group, code="catalog.read")
        staff = AdminAccount.objects.create_user("shipping-staff", "Safe staff passphrase 2026!",
                                                  display_name="员工", kind="STAFF")
        staff.permission_groups.add(group)
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        self.assertEqual(self._login(client, "shipping-staff", "Safe staff passphrase 2026!").status_code, 200)
        self.assertEqual(client.get("/api/v1/admin/settlement/shipping-policy").status_code, 403)
        self.assertEqual(self._put(client, {"feeFen": 500, "deliveryScope": "NATIONWIDE",
                                           "expectedRevision": 1}).status_code, 403)
