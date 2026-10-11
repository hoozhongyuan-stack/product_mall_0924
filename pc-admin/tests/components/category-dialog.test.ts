import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, describe, it, expect, vi } from "vitest";
import { defineComponent } from "vue";
import { confirmAction } from "../../src/shared/confirm";
import CategoryManagement from "../../src/views/catalog/CategoryManagement.vue";
import { api, ApiError, type Account } from "../../src/api";
vi.mock("../../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/api")>()),
  api: vi.fn(),
}));
vi.mock("vue-router", () => ({ onBeforeRouteLeave: vi.fn() }));
vi.mock("../../src/shared/confirm", () => ({
  confirmAction: vi.fn().mockResolvedValue(true),
}));
const root = {
  id: "root",
  parentId: null,
  name: "礼盒",
  sortOrder: 1,
  status: "ACTIVE",
  revision: 2,
  image: {
    assetId: "square",
    adminUrl: "/square",
    kind: "IMAGE",
    width: 100,
    height: 100,
  },
};
const child = {
  id: "child",
  parentId: "root",
  name: "商务礼盒",
  sortOrder: 1,
  status: "ACTIVE",
  revision: 3,
  banner: {
    assetId: "wide",
    adminUrl: "/wide",
    kind: "IMAGE",
    width: 600,
    height: 200,
  },
};
const dialog = defineComponent({
  name: "ElDialog",
  props: [
    "modelValue",
    "title",
    "showClose",
    "closeOnPressEscape",
    "closeOnClickModal",
  ],
  emits: ["update:modelValue"],
  template:
    '<section v-if="modelValue" role="dialog" :aria-label="title"><slot/><slot name="footer"/></section>',
});
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.resetAllMocks();
});
async function setup(
  permissionCodes = ["catalog.read", "catalog.write", "asset.upload"],
) {
  vi.mocked(api).mockResolvedValue([root, child]);
  const w = mount(CategoryManagement, {
    props: { account: { accountId: "a", permissionCodes } as Account },
    global: {
      stubs: {
        ElDialog: dialog,
        CategoryImageEditor: {
          name: "CategoryImageEditor",
          props: ["asset", "secondary", "disabled"],
          emits: ["busyChange", "update:asset"],
          template: '<div class="image-editor"/>',
        },
      },
    },
  });
  wrappers.push(w);
  await flushPromises();
  return w;
}
const button = (w: ReturnType<typeof mount>, text: string) =>
  w.findAll("button").find((b) => b.text() === text)!;
describe("category modal editor", () => {
  it("uses dialog for creation and second level with fresh fields and proper image aspect", async () => {
    const w = await setup();
    expect(w.find("[role=dialog]").exists()).toBe(false);
    await button(w, "新建一级分类").trigger("click");
    expect(w.get("[role=dialog]").attributes("aria-label")).toBe(
      "新建一级分类",
    );
    await w.get('input[placeholder="请输入分类名称"]').setValue("尚未保存");
    await button(w, "取消").trigger("click");
    expect(w.find("[role=dialog]").exists()).toBe(false);
    await button(w, "添加二级").trigger("click");
    expect(w.get("[role=dialog]").attributes("aria-label")).toBe(
      "添加二级分类",
    );
    expect(w.get('input[placeholder="请输入分类名称"]').element).toHaveProperty(
      "value",
      "",
    );
    expect(w.get("select").element).toHaveProperty("value", "root");
    expect(
      w.getComponent({ name: "CategoryImageEditor" }).props("secondary"),
    ).toBe(true);
  });
  it("edits a second level banner and submits expected revision then closes", async () => {
    const w = await setup();
    await w
      .findAll("button")
      .filter((b) => b.text() === "编辑")[1]!
      .trigger("click");
    expect(w.get("[role=dialog]").attributes("aria-label")).toBe(
      "编辑分类 · 商务礼盒",
    );
    expect(
      w.getComponent({ name: "CategoryImageEditor" }).props("asset"),
    ).toMatchObject({ assetId: "wide" });
    vi.mocked(api).mockImplementation(async (path, init) =>
      init ? child : [root, child],
    );
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(api).toHaveBeenCalledWith("/categories/child", {
      method: "PATCH",
      body: JSON.stringify({
        name: "商务礼盒",
        sortOrder: 1,
        imageAssetId: null,
        bannerAssetId: "wide",
        status: "ACTIVE",
        expectedRevision: 3,
      }),
    });
    expect(w.find("[role=dialog]").exists()).toBe(false);
    await button(w, "新建一级分类").trigger("click");
    expect(button(w, "保存分类").attributes("disabled")).toBeUndefined();
  });
  it("locks fields, dismissal and reopening while save is pending", async () => {
    const w = await setup();
    await button(w, "新建一级分类").trigger("click");
    await w.get('input[placeholder="请输入分类名称"]').setValue("新分类");
    let finish: (value: unknown) => void = () => {};
    vi.mocked(api).mockImplementation((path, init) =>
      init
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : Promise.resolve([root, child]),
    );
    await w.get("form").trigger("submit");
    expect(
      w.get('input[placeholder="请输入分类名称"]').attributes("disabled"),
    ).toBeDefined();
    expect(button(w, "取消").attributes("disabled")).toBeDefined();
    const modal = w.getComponent({ name: "ElDialog" });
    expect(modal.props("closeOnPressEscape")).toBe(false);
    expect(modal.props("showClose")).toBe(false);
    modal.vm.$emit("update:modelValue", false);
    await flushPromises();
    expect(w.find("[role=dialog]").exists()).toBe(true);
    finish(root);
    await flushPromises();
    expect(w.find("[role=dialog]").exists()).toBe(false);
  });
  it("shows errors inside dialog and blocks stale revision edits until reopening", async () => {
    const w = await setup();
    await button(w, "编辑").trigger("click");
    await w.get('input[placeholder="请输入分类名称"]').setValue("");
    await w.get("form").trigger("submit");
    expect(w.get("[role=dialog] [role=alert]").text()).toContain("分类名称");
    await w.get('input[placeholder="请输入分类名称"]').setValue("新名");
    vi.mocked(api).mockImplementation(async (path, init) => {
      if (init) throw new ApiError("冲突", 409, "REVISION_CONFLICT");
      return [{ ...root, revision: 3 }, child];
    });
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(w.get("[role=dialog] [role=alert]").text()).toContain("其他人修改");
    expect(button(w, "保存分类").attributes("disabled")).toBeDefined();
    vi.mocked(confirmAction).mockResolvedValueOnce(true);
    await button(w, "取消").trigger("click");
    await flushPromises();
    expect(w.find("[role=dialog]").exists()).toBe(false);
    await button(w, "编辑").trigger("click");
    expect(button(w, "保存分类").attributes("disabled")).toBeUndefined();
  });
  it("blocks saving while upload busy and closes on permission revocation", async () => {
    const w = await setup();
    await button(w, "新建一级分类").trigger("click");
    w.getComponent({ name: "CategoryImageEditor" }).vm.$emit(
      "busyChange",
      true,
    );
    await flushPromises();
    expect(button(w, "保存分类").attributes("disabled")).toBeDefined();
    await w.setProps({
      account: { accountId: "a", permissionCodes: ["catalog.read"] } as Account,
    });
    await flushPromises();
    expect(w.find("[role=dialog]").exists()).toBe(false);
    expect(w.findAll("button").some((b) => b.text() === "新建一级分类")).toBe(
      false,
    );
  });
});

it("asks before discarding edits and preserves the draft when cancellation is declined", async () => {
  const w = await setup();
  await button(w, "新建一级分类").trigger("click");
  await w.get('input[placeholder="请输入分类名称"]').setValue("草稿");
  vi.mocked(confirmAction).mockResolvedValueOnce(false);
  await button(w, "取消").trigger("click");
  await flushPromises();
  expect(w.find("[role=dialog]").exists()).toBe(true);
  expect(w.get("input").element).toHaveProperty("value", "草稿");
  vi.mocked(confirmAction).mockResolvedValueOnce(true);
  await button(w, "取消").trigger("click");
  await flushPromises();
  expect(w.find("[role=dialog]").exists()).toBe(false);
});
it("ignores an old deactivation confirmation after closing and reopening the same category", async () => {
  const w = await setup();
  vi.mocked(api).mockResolvedValue([{ ...root, productCount: 1 }, child]);
  await w.setProps({
    account: {
      accountId: "b",
      permissionCodes: ["catalog.read", "catalog.write"],
    } as Account,
  });
  await flushPromises();
  await button(w, "编辑").trigger("click");
  await w.get("select").setValue("INACTIVE");
  let finish: (value: boolean) => void = () => {};
  vi.mocked(confirmAction).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await w.get("form").trigger("submit");
  vi.mocked(confirmAction).mockResolvedValueOnce(true);
  await button(w, "取消").trigger("click");
  await flushPromises();
  await button(w, "编辑").trigger("click");
  await w.get('input[placeholder="请输入分类名称"]').setValue("新会话字段");
  finish(true);
  await flushPromises();
  expect(vi.mocked(api).mock.calls.every((call) => !call[1])).toBe(true);
  expect(w.get("input").element).toHaveProperty("value", "新会话字段");
});
it("preserves list read failure after revision conflict and allows reloading after close", async () => {
  const w = await setup();
  await button(w, "编辑").trigger("click");
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (init) throw new ApiError("冲突", 409, "REVISION_CONFLICT");
    throw new Error("读取失败");
  });
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(w.get("[role=dialog] [role=alert]").text()).toContain(
    "读取最新分类失败",
  );
  await button(w, "取消").trigger("click");
  await flushPromises();
  expect(w.get("[role=alert]").text()).toContain("读取失败");
  expect(button(w, "编辑").attributes("disabled")).toBeDefined();
  vi.mocked(api).mockResolvedValue([root, child]);
  await button(w, "重新加载").trigger("click");
  await flushPromises();
  await button(w, "编辑").trigger("click");
  expect(w.find("[role=dialog]").exists()).toBe(true);
});
it("keeps submission failures inside the modal and ignores confirmation after unmount", async () => {
  const w = await setup();
  await button(w, "编辑").trigger("click");
  vi.mocked(api).mockRejectedValue(new Error("保存失败"));
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(w.get("[role=dialog] [role=alert]").text()).toBe("保存失败");
  expect(w.findAll("[role=alert]")).toHaveLength(1);
  await button(w, "取消").trigger("click");
  await flushPromises();
  vi.mocked(api).mockResolvedValue([{ ...root, productCount: 1 }, child]);
  await w.setProps({
    account: {
      accountId: "b",
      permissionCodes: ["catalog.read", "catalog.write"],
    } as Account,
  });
  await flushPromises();
  await button(w, "编辑").trigger("click");
  await w.get("select").setValue("INACTIVE");
  let finish: (value: boolean) => void = () => {};
  vi.mocked(confirmAction).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await w.get("form").trigger("submit");
  const calls = vi.mocked(api).mock.calls.length;
  w.unmount();
  finish(true);
  await flushPromises();
  expect(api).toHaveBeenCalledTimes(calls);
});
