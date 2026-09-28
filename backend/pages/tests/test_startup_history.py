"""Startup historical preview, private assets and rollback boundaries."""
from unittest.mock import patch

from django.db import DatabaseError, transaction
from django.test import TestCase, Client

from accounts.models import AdminAccount, PermissionGroup, GroupPermission
from catalog.media import asset_path
from pages.models import StartupConfigVersion, StartupPublishRequest, StartupPublication
from .test_startup_flow import StartupFlowTests, PASSWORD, DRAFT


class StartupHistoryTests(TestCase):
    setUp = StartupFlowTests.setUp
    send = StartupFlowTests.send
    logged_in = StartupFlowTests.logged_in
    asset = StartupFlowTests.asset
    save = StartupFlowTests.save
    confirmation = StartupFlowTests.confirmation
    publish = StartupFlowTests.publish

    def two_versions(self):
        gif, fallback = self.asset("GIF"), self.asset("IMAGE")
        first = self.save(gif, fallback, 1).json()["data"]
        a = self.publish(first["revision"], key="startup-history-a").json()["data"]
        second = self.save(self.asset("GIF"), self.asset("IMAGE"), first["revision"]).json()["data"]
        b = self.publish(second["revision"], key="startup-history-b").json()["data"]
        return first, second, a, b, gif, fallback

    def rollback(self, second, target, epoch=2, key="startup-rollback-key", token=None):
        if token is None:
            confirmed = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
                "action": "startup.rollback", "password": PASSWORD,
                "objectId": "startup:" + target["versionId"], "revision": epoch})
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
            token = confirmed.json()["data"]["confirmationToken"]
        return self.send(self.client, "post", "/api/v1/admin/startup/rollback", {
            "expectedRevision": second["revision"], "expectedPublicationRevision": epoch,
            "versionId": target["versionId"], "reason": "恢复旧品牌素材"}, key=key, confirmation=token)

    def reader(self, domain):
        group = PermissionGroup.objects.create(code=domain + "-history", name=domain + "历史只读")
        GroupPermission.objects.create(group=group, code=domain + ".read")
        actor = AdminAccount.objects.create_user(domain + "-reader", PASSWORD, kind="STAFF")
        actor.permission_groups.add(group)
        return self.logged_in(actor)

    def test_history_private_preview_public_boundary_and_rollback_replay(self):
        _, second, a, b, gif, fallback = self.two_versions()
        reader = self.reader("startup")
        page_reader = self.reader("page")
        endpoint = "/api/v1/admin/startup/versions"
        history = reader.get(endpoint + "?pageSize=1")
        self.assertEqual(history.status_code, 200)
        self.assertIn("no-store", history["Cache-Control"])
        self.assertIn("Cookie", history["Vary"])
        self.assertEqual(history.json()["data"]["total"], 2)
        detail = reader.get(endpoint + "/" + a["versionId"]).json()["data"]
        self.assertEqual(detail["publishedBy"], "主账号")
        for field in ("gifUrl", "fallbackUrl"):
            self.assertIn("/admin/", detail[field])
            response = reader.get(detail[field])
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content))
            self.assertEqual(page_reader.get(detail[field]).status_code, 403)
        self.assertEqual(Client().get("/api/v1/app/assets/" + str(gif.id) + "/file").status_code, 404)
        self.assertEqual(page_reader.get(endpoint).status_code, 403)
        self.assertEqual(reader.get(endpoint + "/" + "00000000-0000-0000-0000-000000000001").status_code, 404)
        rolled = self.rollback(second, a)
        self.assertEqual(rolled.status_code, 200, rolled.content)
        self.assertEqual(rolled.json()["data"]["publicationRevision"], 3)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["gifAssetId"], second["gifAssetId"])
        self.assertEqual(self.rollback(second, b, epoch=3, key="startup-back-key").status_code, 200)
        replay = self.rollback(second, a)
        self.assertEqual(replay.json()["data"], rolled.json()["data"])
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], b["versionId"])
        self.assertEqual(StartupConfigVersion.objects.count(), 2)

    def test_invalid_fallback_and_io_failure_preserve_current(self):
        _, second, a, b, gif, fallback = self.two_versions()
        asset_path(fallback).write_bytes(b"bad PNG")
        self.assertEqual(self.rollback(second, a).status_code, 422)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], b["versionId"])
        with patch("pages.startup_views._assets", side_effect=OSError("temporary read failure")):
            self.assertEqual(self.rollback(second, a).status_code, 503)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publicationRevision"], 2)

    def test_startup_versions_and_receipts_reject_update_and_delete(self):
        _, _, a, _, _, _ = self.two_versions()
        for model, values in ((StartupConfigVersion, {"revision": 9}),
                              (StartupPublishRequest, {"result": {}})):
            with self.assertRaises(DatabaseError), transaction.atomic():
                model.objects.update(**values)
            with self.assertRaises(DatabaseError), transaction.atomic():
                model.objects.all().delete()

    def test_publication_cannot_be_deleted_or_reset_to_unpublished(self):
        self.two_versions()
        queryset = StartupPublication.objects.filter(config_id=1)
        for action in (lambda: queryset.delete(), lambda: queryset.update(current_version=None, revision=3)):
            with self.assertRaises(DatabaseError), transaction.atomic():
                action()

    def test_rollback_rejects_wrong_target_confirmation_and_changed_draft(self):
        _, second, a, b, _, _ = self.two_versions()
        confirmed = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
            "action": "startup.rollback", "password": PASSWORD,
            "objectId": "startup:" + b["versionId"], "revision": 2})
        result = self.rollback(second, a, token=confirmed.json()["data"]["confirmationToken"])
        self.assertEqual(result.status_code, 403)
        saved = self.save(self.asset("GIF"), self.asset("IMAGE"), second["revision"])
        self.assertEqual(saved.status_code, 200, saved.content)
        stale = self.rollback(second, a)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], b["versionId"])
