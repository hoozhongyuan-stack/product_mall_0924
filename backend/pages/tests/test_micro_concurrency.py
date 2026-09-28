"""Real PostgreSQL connections exercising the publication graph lock."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import Client, TransactionTestCase

from accounts.models import AdminAccount
from pages.models import MicroPage, PageConfigVersion, PagePublication
from pages.views import _lock_publication_graph


PASSWORD = "Safe owner passphrase 2026!"


class ConcurrentMicroPublicationTests(TransactionTestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "micro-concurrent-owner", PASSWORD, display_name="主账号",
            kind=AdminAccount.Kind.OWNER)
        self.first = self.published_page("甲")
        self.second = self.published_page("乙")
        self.set_link(self.first, self.second)
        self.set_link(self.second, self.first)

    def published_page(self, name):
        config = {"schemaVersion": 1, "pageType": "MICRO",
                  "theme": {"pageBackgroundColor": "#F7F5F1",
                            "headerBackgroundColor": "#FFFFFF", "brandTextColor": "#25221F"},
                  "components": []}
        page = MicroPage.objects.create(page_type="MICRO", name=name, draft_config=config)
        version = PageConfigVersion.objects.create(
            page=page, revision=1, name=name, config_json=config, published_by=self.owner)
        PagePublication.objects.create(page=page, current_version=version, revision=1)
        return page

    def set_link(self, page, target):
        page.draft_config = {**page.draft_config, "components": [{
            "componentId": "link", "type": "NOTICE", "sortOrder": 10, "visible": True,
            "props": {"text": "查看", "link": {"type": "PAGE", "targetId": str(target.id)}},
        }]}
        page.draft_revision = 2
        page.save(update_fields=["draft_config", "draft_revision"])

    def client_and_confirmation(self, page):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        login = client.post("/api/v1/admin/auth/login",
                            data=json.dumps({"loginName": self.owner.login_name, "password": PASSWORD}),
                            content_type="application/json", **headers)
        self.assertEqual(login.status_code, 200, login.content)
        headers["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
        confirmed = client.post("/api/v1/admin/auth/confirm", data=json.dumps({
            "action": "page.publish", "password": PASSWORD,
            "objectId": str(page.id), "revision": page.draft_revision,
        }), content_type="application/json", **headers)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return client, confirmed.json()["data"]["confirmationToken"]

    def test_two_connections_cannot_publish_a_page_cycle(self):
        client_a, token_a = self.client_and_confirmation(self.first)
        client_b, token_b = self.client_and_confirmation(self.second)
        barrier = Barrier(2, timeout=15)

        def arrive_together():
            barrier.wait()
            _lock_publication_graph()

        def publish(client, page, token, key):
            try:
                return client.post(
                    f"/api/v1/admin/pages/{page.id}/publish",
                    data=json.dumps({"expectedRevision": page.draft_revision,
                                     "expectedPublicationRevision": page.pagepublication.revision}),
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
                    HTTP_IDEMPOTENCY_KEY=key,
                    HTTP_X_ACTION_CONFIRMATION=token,
                )
            finally:
                connections.close_all()

        with patch("pages.views._lock_publication_graph", side_effect=arrive_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_result = pool.submit(publish, client_a, self.first, token_a, "concurrent-page-a")
                second_result = pool.submit(publish, client_b, self.second, token_b, "concurrent-page-b")
                responses = [first_result.result(timeout=20), second_result.result(timeout=20)]

        self.assertEqual(sorted(item.status_code for item in responses), [200, 422])
        self.assertEqual(PageConfigVersion.objects.filter(revision=2).count(), 1)
        self.assertEqual([item.json()["error"]["code"] for item in responses if item.status_code == 422],
                         ["PUBLISH_TARGET_INVALID"])
