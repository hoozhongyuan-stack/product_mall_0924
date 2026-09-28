import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Asset
from pages.models import MicroPage, PageConfigVersion, PagePublication


PASSWORD = "Safe owner passphrase 2026!"
PAGES = "/api/v1/admin/pages"


class MicroFlowTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "micro-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self.login(self.owner)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media_root = Path(media.name)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def send(self, client, verb, path, body=None, *, key=None, confirmation=None):
        if path.endswith("/publish") and body is not None and set(body) == {"expectedRevision"}:
            draft = client.get(path[:-len("publish")] + "draft")
            if draft.status_code == 200:
                epochs = getattr(self, "publish_epochs", {})
                identity = (id(client), path, key, body["expectedRevision"])
                epoch = epochs.get(identity, draft.json()["data"]["publicationRevision"])
                self.publish_epochs = {**epochs, identity: epoch}
                body = {**body, "expectedPublicationRevision": epoch}
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if confirmation:
            headers["HTTP_X_ACTION_CONFIRMATION"] = confirmation
        return getattr(client, verb)(path, data=json.dumps(body or {}), content_type="application/json", **headers)

    def login(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.send(client, "post", "/api/v1/admin/auth/login",
                           {"loginName": account.login_name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200)
        return client

    def create(self, name="活动页"):
        result = self.send(self.client, "post", PAGES, {"name": name})
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()["data"]

    def draft_path(self, page):
        return f"{PAGES}/{page['pageId']}/draft"

    def save(self, page, config, *, name=None, revision=None):
        return self.send(self.client, "put", self.draft_path(page), {
            "name": name or page["name"], "expectedRevision": revision or page["revision"], "config": config})

    def page_link_config(self, page, target, *, visible=True):
        config = {**page["config"], "components": [{
            "componentId": "link", "type": "NOTICE", "sortOrder": 10, "visible": visible,
            "props": {"text": "查看", "link": {"type": "PAGE", "targetId": target}},
        }]}
        return config

    def confirm(self, page):
        result = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
            "action": "page.publish", "password": PASSWORD,
            "objectId": page["pageId"], "revision": page["revision"]})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def publish(self, page, *, key="micro-publish-key-123"):
        return self.send(self.client, "post", f"{PAGES}/{page['pageId']}/publish",
                         {"expectedRevision": page["revision"]}, key=key, confirmation=self.confirm(page))

    def test_create_list_draft_publish_and_public_visible_components(self):
        first = self.create("秋季活动")
        self.create("冬季活动")
        self.assertEqual(first["config"]["pageType"], "MICRO")
        self.assertEqual(first["publishedRevision"], None)
        listing = self.client.get(f"{PAGES}?page=1&pageSize=1").json()["data"]
        self.assertEqual((listing["page"], listing["pageSize"], listing["total"]), (1, 1, 2))
        self.assertEqual(len(listing["rows"]), 1)
        self.assertEqual(Client().get(f"/api/v1/app/pages/{first['pageId']}").status_code, 404)
        config = self.page_link_config(first, str(uuid.uuid4()), visible=False)
        config["components"][0]["props"].pop("link")
        saved = self.save(first, config, name="秋季特辑")
        self.assertEqual(saved.status_code, 200, saved.content)
        saved_page = saved.json()["data"]
        self.assertEqual(saved_page["revision"], first["revision"] + 1)
        self.assertEqual(self.client.get(self.draft_path(first)).json()["data"]["name"], "秋季特辑")
        preview = self.send(self.client, "post", f"{PAGES}/{first['pageId']}/preview",
                            {"expectedRevision": saved_page["revision"]})
        self.assertEqual(preview.status_code, 200, preview.content)
        published = self.publish(saved_page)
        self.assertEqual(published.status_code, 200, published.content)
        public = Client().get(f"/api/v1/app/pages/{first['pageId']}")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()["data"]["name"], "秋季特辑")
        self.assertEqual(public.json()["data"]["config"]["components"], [])
        renamed = self.save(saved_page, saved_page["config"], name="未发布的新名称")
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/pages/{first['pageId']}").json()["data"]["name"],
                         "秋季特辑")
        self.assertTrue(AuditLog.objects.filter(action_code="page.publish", object_id=first["pageId"]).exists())

    def test_page_link_requires_published_micro_and_rejects_self_and_cycle(self):
        first, second = self.create("甲"), self.create("乙")
        self.assertEqual(self.save(first, self.page_link_config(first, first["pageId"])).status_code, 400)
        draft = self.save(first, self.page_link_config(first, second["pageId"]))
        self.assertEqual(draft.status_code, 200, draft.content)
        first = draft.json()["data"]
        preview = self.send(self.client, "post", f"{PAGES}/{first['pageId']}/preview",
                            {"expectedRevision": first["revision"]})
        self.assertEqual(preview.status_code, 422)
        self.assertEqual(self.publish(first).status_code, 422)
        self.assertEqual(self.publish(second).status_code, 200)
        self.assertEqual(self.publish(first, key="micro-publish-key-456").status_code, 200)
        later = self.save(second, self.page_link_config(second, first["pageId"]))
        self.assertEqual(later.status_code, 200, later.content)
        self.assertEqual(self.publish(later.json()["data"], key="micro-publish-key-789").status_code, 422)
        self.assertEqual(PageConfigVersion.objects.filter(page_id=second["pageId"]).count(), 1)

    def test_home_can_link_to_published_micro_but_not_home(self):
        page = self.create()
        self.assertEqual(self.publish(page).status_code, 200)
        home = self.client.get("/api/v1/admin/pages/home/draft").json()["data"]
        config = self.page_link_config(home, page["pageId"])
        saved = self.send(self.client, "put", "/api/v1/admin/pages/home/draft",
                          {"expectedRevision": home["revision"], "config": config})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(self.save(page, self.page_link_config(page, home["pageId"])).status_code, 400)

    def test_permissions_conflicts_and_idempotency(self):
        page = self.create()
        group = PermissionGroup.objects.create(code="micro-reader", name="微页面只读")
        GroupPermission.objects.create(group=group, code="page.read")
        reader = AdminAccount.objects.create_user("micro-reader", PASSWORD, display_name="只读")
        reader.permission_groups.add(group)
        client = self.login(reader)
        self.assertEqual(client.get(PAGES).status_code, 200)
        self.assertEqual(client.get(self.draft_path(page)).status_code, 200)
        self.assertEqual(self.send(client, "post", PAGES, {"name": "越权"}).status_code, 403)
        self.assertEqual(self.send(client, "put", self.draft_path(page), {}).status_code, 403)
        self.assertEqual(self.send(client, "post", f"{PAGES}/{page['pageId']}/publish", {}).status_code, 403)
        changed = self.save(page, page["config"]).json()["data"]
        self.assertEqual(self.save(page, page["config"], revision=page["revision"]).status_code, 409)
        result = self.publish(changed)
        self.assertEqual(result.status_code, 200, result.content)
        same = self.send(self.client, "post", f"{PAGES}/{page['pageId']}/publish",
                         {"expectedRevision": changed["revision"]}, key="micro-publish-key-123")
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.json()["data"]["versionId"], result.json()["data"]["versionId"])
        reused = self.send(self.client, "post", f"{PAGES}/{page['pageId']}/publish",
                           {"expectedRevision": page["revision"]}, key="micro-publish-key-123")
        self.assertEqual(reused.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_failed_publish_keeps_previous_version_and_media_private(self):
        page = self.create()
        first = self.publish(page).json()["data"]
        unpublished_target = self.create("尚未发布的目标")
        asset_id = uuid.uuid4()
        path = self.media_root / f"page/{asset_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-image")
        Asset.objects.create(id=asset_id, kind="IMAGE", content_type="image/png", byte_size=10,
                             width=1, height=1, sha256="0" * 64, original_name="banner.png",
                             stored_name=f"page/{asset_id}.png", created_by=self.owner)
        config = {**page["config"], "components": [{"componentId": "hero", "type": "CAROUSEL",
            "sortOrder": 10, "visible": True, "props": {"slides": [{"assetId": str(asset_id),
            "link": {"type": "PAGE", "targetId": unpublished_target["pageId"]}}]}}]}
        changed = self.save(page, config).json()["data"]
        self.assertEqual(self.publish(changed, key="micro-publish-key-456").status_code, 422)
        current = Client().get(f"/api/v1/app/pages/{page['pageId']}").json()["data"]
        self.assertEqual(current["versionId"], first["versionId"])
        self.assertEqual(Client().get(f"/api/v1/app/assets/{asset_id}/file").status_code, 404)
        self.assertEqual(PagePublication.objects.get(page_id=page["pageId"]).current_version_id,
                         uuid.UUID(first["versionId"]))

    def test_micro_visible_media_only_and_atomic_failure(self):
        page = self.create()
        assets = []
        for _ in range(2):
            asset_id = uuid.uuid4()
            path = self.media_root / f"page/{asset_id}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-image")
            assets.append(Asset.objects.create(
                id=asset_id, kind="IMAGE", content_type="image/png", byte_size=10,
                width=1, height=1, sha256="0" * 64, original_name="banner.png",
                stored_name=f"page/{asset_id}.png", created_by=self.owner))
        config = {**page["config"], "components": [
            {"componentId": "hero", "type": "CAROUSEL", "sortOrder": 10,
             "visible": True, "props": {"slides": [{"assetId": str(assets[0].id)}]}},
            {"componentId": "hidden", "type": "IMAGE_HOTZONE", "sortOrder": 20,
             "visible": False, "props": {"assetId": str(assets[1].id), "areas": []}},
        ]}
        page = self.save(page, config).json()["data"]
        with patch("pages.views.audit", side_effect=RuntimeError("simulated audit failure")):
            with self.assertRaises(RuntimeError):
                self.publish(page)
        self.assertEqual(PageConfigVersion.objects.filter(page_id=page["pageId"]).count(), 0)
        self.assertEqual(Client().get(f"/api/v1/app/pages/{page['pageId']}").status_code, 404)
        published = self.publish(page, key="micro-publish-key-456")
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{assets[0].id}/file").status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{assets[1].id}/file").status_code, 404)
        next_page = self.save(page, {**page["config"], "components": []}).json()["data"]
        self.assertEqual(self.publish(next_page, key="micro-publish-key-789").status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{assets[0].id}/file").status_code, 404)
