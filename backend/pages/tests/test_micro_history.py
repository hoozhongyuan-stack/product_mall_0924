"""Rollback validates the current published link graph without overwriting drafts."""
from django.test import TestCase

from .test_micro_flow import MicroFlowTests, PASSWORD, PAGES


class MicroHistoryTests(TestCase):
    setUp = MicroFlowTests.setUp
    send = MicroFlowTests.send
    login = MicroFlowTests.login
    create = MicroFlowTests.create
    draft_path = MicroFlowTests.draft_path
    save = MicroFlowTests.save
    page_link_config = MicroFlowTests.page_link_config
    confirm = MicroFlowTests.confirm
    publish = MicroFlowTests.publish

    def rollback(self, page, version, epoch):
        confirmed = self.send(self.client, "post", "/api/v1/admin/auth/confirm", {
            "action": "page.rollback", "password": PASSWORD,
            "objectId": page["pageId"] + ":" + version["versionId"], "revision": epoch})
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return self.send(self.client, "post", f"{PAGES}/{page['pageId']}/rollback", {
            "expectedRevision": page["revision"], "expectedPublicationRevision": epoch,
            "versionId": version["versionId"], "reason": "恢复活动页面"}, key="e1-micro-rollback",
            confirmation=confirmed.json()["data"]["confirmationToken"])

    def test_rollback_rejects_new_cycle_in_historical_page_links(self):
        first, second = self.create("甲"), self.create("乙")
        self.assertEqual(self.publish(second, key="e1-micro-b-initial").status_code, 200)
        first = self.save(first, self.page_link_config(first, second["pageId"])).json()["data"]
        old = self.publish(first, key="e1-micro-a-linked").json()["data"]
        first = self.save(first, {**first["config"], "components": []}).json()["data"]
        current = self.publish(first, key="e1-micro-a-empty").json()["data"]
        second = self.save(second, self.page_link_config(second, first["pageId"])).json()["data"]
        self.assertEqual(self.publish(second, key="e1-micro-b-linked").status_code, 200)
        rejected = self.rollback(first, old, 2)
        self.assertEqual(rejected.status_code, 422, rejected.content)
        draft = self.client.get(self.draft_path(first)).json()["data"]
        self.assertEqual(draft["publicationRevision"], 2)
        self.assertEqual(draft["publishedVersionId"], current["versionId"])
        self.assertEqual(draft["config"], first["config"])

    def test_history_lookup_is_scoped_to_selected_page(self):
        first, second = self.create("甲"), self.create("乙")
        version = self.publish(first, key="e1-micro-history-first").json()["data"]
        self.assertEqual(self.client.get(f"{PAGES}/{first['pageId']}/versions").json()["data"]["total"], 1)
        self.assertEqual(self.client.get(f"{PAGES}/{second['pageId']}/versions/{version['versionId']}").status_code, 404)
