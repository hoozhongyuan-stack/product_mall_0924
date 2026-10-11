import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, describe, it, expect, vi } from "vitest";
import FilingSettingsPanel from "../../src/views/FilingSettingsPanel.vue";
import { api, type Account } from "../../src/api";
vi.mock("../../src/api", () => ({ api: vi.fn() }));
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.resetAllMocks();
});
describe("filing configuration", () => {
  it("persists blank to hide and rechecks revision after unknown save", async () => {
    vi.mocked(api).mockResolvedValue({ revision: 1, recordNo: "测试备案号" });
    const w = mount(FilingSettingsPanel, {
      props: {
        account: {
          accountId: "a",
          permissionCodes: ["navigation.read", "navigation.edit"],
        } as Account,
      },
    });
    wrappers.push(w);
    await flushPromises();
    await w.get("input").setValue("");
    vi.mocked(api).mockResolvedValue({ revision: 2, recordNo: "" });
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(api).toHaveBeenLastCalledWith("/storefront/filing", {
      method: "PUT",
      body: JSON.stringify({ expectedRevision: 1, recordNo: "" }),
    });
    expect(w.text()).toContain("留空时隐藏");
    await w.get("input").setValue("新号");
    vi.mocked(api).mockRejectedValue(new Error("network"));
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(w.text()).toContain("重新读取后核对");
    expect(w.get("button.primary-button").attributes("disabled")).toBeDefined();
  });
});
it("handles load errors and resets on account revocation without displaying stale filing", async () => {
  vi.mocked(api).mockRejectedValue(new Error("network"));
  const w = mount(FilingSettingsPanel, {
    props: {
      account: {
        accountId: "a",
        permissionCodes: ["navigation.read", "navigation.edit"],
      } as Account,
    },
  });
  wrappers.push(w);
  await flushPromises();
  expect(w.text()).toContain("读取失败");
  vi.mocked(api).mockResolvedValue({ revision: 1, recordNo: "备案" });
  await w.get("button.secondary-button").trigger("click");
  await flushPromises();
  expect(w.get("input").element).toHaveProperty("value", "备案");
  await w.setProps({
    account: { accountId: "b", permissionCodes: [] } as Account,
  });
  await flushPromises();
  expect(w.text()).not.toContain("备案");
});
