"""E1 history and rollback preserve drafts, immutable versions and ABA fences."""
import copy
import uuid
from unittest.mock import patch

from django.db import DatabaseError, transaction
from django.test import TestCase

from pages.models import MicroPage, PageConfigVersion, PagePublication
from .test_home_flow import HomeFlowTests, PASSWORD, DRAFT


class HistoryTests(TestCase):
    setUp = HomeFlowTests.setUp
    send = HomeFlowTests.send
    logged_in = HomeFlowTests.logged_in
    image = HomeFlowTests.image
    config = HomeFlowTests.config
    save = HomeFlowTests.save
    confirmation = HomeFlowTests.confirmation
    publish = HomeFlowTests.publish

    def two_versions(self):
        first = self.save(self.config(asset=self.image())).json()["data"]
        a = self.publish(first["revision"], first["pageId"], key="e1-publish-first").json()["data"]
        config = copy.deepcopy(first["config"])
        config["components"][1]["props"]["text"] = "新版"
        second = self.save(config).json()["data"]
        b = self.publish(second["revision"], second["pageId"], key="e1-publish-second").json()["data"]
        return first, second, a, b

    def rollback(self, page, target, publication_revision, key="e1-rollback-key", *, reason="纠正误发布", token=True):
        confirmed = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
            "action": "page.rollback", "password": PASSWORD,
            "objectId": page["pageId"] + ":" + target["versionId"], "revision": publication_revision})
        if token:
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return self.send(self.client, "post", "/api/v1/admin/pages/home/rollback", {
            "expectedRevision": page["revision"], "expectedPublicationRevision": publication_revision,
            "versionId": target["versionId"], "reason": reason}, key=key,
            confirmation=confirmed.json()["data"]["confirmationToken"] if token else None)

    def test_history_pagination_detail_and_draft_publication_metadata(self):
        _, second, a, b = self.two_versions()
        result = self.client.get("/api/v1/admin/pages/home/versions?pageSize=1")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["list"][0]["versionId"], b["versionId"])
        self.assertTrue(data["list"][0]["isCurrent"])
        self.assertEqual(data["publicationRevision"], 2)
        self.assertEqual(data["draftRevision"], second["revision"])
        detail = self.client.get("/api/v1/admin/pages/home/versions/" + a["versionId"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["config"]["components"][1]["props"]["text"], "新品上架")
        draft = self.client.get(DRAFT).json()["data"]
        self.assertEqual(draft["publishedVersionId"], b["versionId"])
        self.assertEqual(draft["publicationRevision"], 2)

    def test_rollback_preserves_draft_and_replay_cannot_restore_old_pointer(self):
        _, second, a, b = self.two_versions()
        rolled = self.rollback(second, a, 2)
        self.assertEqual(rolled.status_code, 200, rolled.content)
        self.assertEqual(rolled.json()["data"]["publicationRevision"], 3)
        draft = self.client.get(DRAFT).json()["data"]
        self.assertEqual(draft["config"], second["config"])
        self.assertEqual(draft["revision"], second["revision"])
        self.assertEqual(PageConfigVersion.objects.count(), 2)
        self.assertEqual(self.rollback(second, b, 3, key="e1-rollback-back").status_code, 200)
        replay = self.rollback(second, a, 2)
        self.assertEqual(replay.json(), {**rolled.json(), "requestId": replay.json()["requestId"]})
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], b["versionId"])
        conflict = self.rollback(second, a, 2, key="e1-new-stale-key")
        self.assertEqual(conflict.status_code, 409)
        collision = self.rollback(second, b, 4, key="e1-rollback-key")
        self.assertEqual(collision.status_code, 409)

    def test_failed_material_validation_keeps_draft_and_publication(self):
        first, second, a, b = self.two_versions()
        from catalog.models import Asset
        from catalog.media import asset_path
        asset = Asset.objects.get(pk=first["config"]["components"][2]["props"]["slides"][0]["assetId"])
        asset_path(asset).unlink()
        failed = self.rollback(second, a, 2)
        self.assertEqual(failed.status_code, 422, failed.content)
        current = self.client.get(DRAFT).json()["data"]
        self.assertEqual(current["publishedVersionId"], b["versionId"])
        self.assertEqual(current["publicationRevision"], 2)
        self.assertEqual(current["config"], second["config"])

    def test_rollback_requires_confirmation_and_nonempty_reason(self):
        _, second, a, _ = self.two_versions()
        self.assertEqual(self.rollback(second, a, 2, token=False).status_code, 403)
        for reason in ("", " " * 3, "x" * 201, 42):
            result = self.rollback(second, a, 2, reason=reason)
            self.assertEqual(result.status_code, 400, result.content)

    def test_audit_failure_rolls_back_pointer_and_idempotency(self):
        _, second, a, b = self.two_versions()
        with patch("pages.history.audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.rollback(second, a, 2)
        draft = self.client.get(DRAFT).json()["data"]
        self.assertEqual(draft["publishedVersionId"], b["versionId"])
        self.assertEqual(draft["publicationRevision"], 2)
        self.assertEqual(self.rollback(second, a, 2).status_code, 200)

    def test_database_rejects_version_mutation_deletion_and_cross_page_pointer(self):
        _, _, a, _ = self.two_versions()
        for operation in (lambda: PageConfigVersion.objects.filter(pk=a["versionId"]).update(name="tamper"),
                          lambda: PageConfigVersion.objects.filter(pk=a["versionId"]).delete()):
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        other = MicroPage.objects.create(page_type="MICRO", name="其他", draft_config={})
        PagePublication.objects.create(page=other)
        with self.assertRaises(DatabaseError), transaction.atomic():
            PagePublication.objects.filter(page=other).update(current_version_id=a["versionId"])

    def test_publication_fence_and_operation_receipts_are_immutable(self):
        _, second, a, _ = self.two_versions()
        from pages.models import ContentRollbackRequest, PagePublishRequest
        publication = PagePublication.objects.get(page_id=second["pageId"])
        for values in ({"revision": 0}, {"revision": 99}, {"current_version_id": a["versionId"]}):
            with self.assertRaises(DatabaseError), transaction.atomic():
                PagePublication.objects.filter(pk=publication.pk).update(**values)
        self.assertEqual(self.rollback(second, a, 2).status_code, 200)
        for model in (PagePublishRequest, ContentRollbackRequest):
            with self.assertRaises(DatabaseError), transaction.atomic():
                model.objects.update(result={"tampered": True})
            with self.assertRaises(DatabaseError), transaction.atomic():
                model.objects.all().delete()

    def test_publication_cannot_be_deleted_or_reset_to_unpublished(self):
        _, second, _, _ = self.two_versions()
        queryset = PagePublication.objects.filter(page_id=second["pageId"])
        for action in (lambda: queryset.delete(), lambda: queryset.update(current_version=None, revision=3)):
            with self.assertRaises(DatabaseError), transaction.atomic():
                action()

    def test_publish_after_successful_rollback_rejects_original_epoch(self):
        _, second, a, _ = self.two_versions()
        third = self.save(second["config"]).json()["data"]
        self.assertEqual(self.rollback(third, a, 2).status_code, 200)
        result = self.send(self.client, "post", "/api/v1/admin/pages/home/publish", {
            "expectedRevision": third["revision"], "expectedPublicationRevision": 2}, key="e1-pub-after-rollback",
            confirmation=self.confirmation(third["pageId"], third["revision"]))
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"]["code"], "PUBLICATION_REVISION_CONFLICT")

    def test_same_current_and_cross_page_targets_are_rejected(self):
        _, second, a, b = self.two_versions()
        self.assertEqual(self.rollback(second, b, 2).json()["error"]["code"], "ALREADY_CURRENT")
        other = MicroPage.objects.create(page_type="MICRO", name="其他", draft_config={})
        PagePublication.objects.create(page=other)
        target = PageConfigVersion.objects.create(page=other, revision=1, name="其他", config_json={},
                                                  published_by=self.owner)
        self.assertEqual(self.rollback(second, {"versionId": str(target.id)}, 2).status_code, 404)

    def test_storage_outage_retains_pointer_and_is_503(self):
        _, second, a, b = self.two_versions()
        from catalog.validation import CatalogError
        with patch("catalog.media.lock_available_assets", side_effect=CatalogError(
                "存储不可用", "MEDIA_STORAGE_UNAVAILABLE", 503)):
            failed = self.rollback(second, a, 2)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], b["versionId"])

    def test_history_rejects_ambiguous_and_out_of_bounds_pagination(self):
        for query in ("page=0", "pageSize=101", "page=x", "page=1&page=2", "unknown=1"):
            self.assertEqual(self.client.get("/api/v1/admin/pages/home/versions?" + query).status_code, 400)

    def test_publish_requires_explicit_integer_publication_revision(self):
        import json
        for epoch in (None, True, -1, "0"):
            body = {"expectedRevision": 1}
            if epoch is not None:
                body = {**body, "expectedPublicationRevision": epoch}
            result = self.client.post("/api/v1/admin/pages/home/publish", data=json.dumps(body),
                content_type="application/json", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
                HTTP_IDEMPOTENCY_KEY="e1-strict-publish")
            self.assertEqual(result.status_code, 400, result.content)

    def test_rollback_rejects_malformed_body_before_consuming_confirmation(self):
        values = {"expectedRevision": 1, "expectedPublicationRevision": 0,
                  "versionId": str(uuid.uuid4()), "reason": "恢复"}
        for body in ({**values, "unexpected": 1}, {**values, "versionId": 42},
                     {**values, "versionId": "bad"}, {**values, "expectedPublicationRevision": True}):
            result = self.send(self.client, "post", "/api/v1/admin/pages/home/rollback", body,
                               key="e1-strict-rollback")
            self.assertEqual(result.status_code, 400, result.content)

    def test_rollback_confirmation_is_bound_to_target_and_publication_epoch(self):
        _, second, a, b = self.two_versions()
        confirmed = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
            "action": "page.rollback", "password": PASSWORD,
            "objectId": second["pageId"] + ":" + b["versionId"], "revision": 2})
        result = self.send(self.client, "post", "/api/v1/admin/pages/home/rollback", {
            "expectedRevision": second["revision"], "expectedPublicationRevision": 2,
            "versionId": a["versionId"], "reason": "恢复"}, key="e1-target-bound-key",
            confirmation=confirmed.json()["data"]["confirmationToken"])
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publicationRevision"], 2)

    def test_rollback_rechecks_category_state(self):
        from catalog.models import Category
        category = Category.objects.create(name="活动分类", status="ACTIVE")
        config = self.config()
        config["components"][1]["props"]["link"] = {"type": "CATEGORY", "targetId": str(category.id)}
        first = self.save(config).json()["data"]
        old = self.publish(first["revision"], first["pageId"], key="e1-category-first").json()["data"]
        second = self.save(self.config()).json()["data"]
        current = self.publish(second["revision"], second["pageId"], key="e1-category-current").json()["data"]
        Category.objects.filter(pk=category.pk).update(status="INACTIVE")
        result = self.rollback(second, old, 2)
        self.assertEqual(result.status_code, 422, result.content)
        self.assertEqual(self.client.get(DRAFT).json()["data"]["publishedVersionId"], current["versionId"])
