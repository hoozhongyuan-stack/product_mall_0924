import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, describe, it, expect, vi } from "vitest";
import CategoryImageEditor from "../../src/views/catalog/CategoryImageEditor.vue";
import { api } from "../../src/api";
import { validateMediaFile } from "../../src/views/catalog/types";
vi.mock("../../src/api", () => ({ api: vi.fn() }));
vi.mock("../../src/views/catalog/types", () => ({
  validateMediaFile: vi.fn(),
}));
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.resetAllMocks();
});
describe("category imagery", () => {
  it("rejects square second level banner and preserves valid wide image", async () => {
    const w = mount(CategoryImageEditor, {
      props: { asset: null, secondary: true, targetKey: "a", canUpload: false },
      global: { stubs: { AssetPicker: true } },
    });
    wrappers.push(w);
    w.getComponent({ name: "AssetPicker" }).vm.$emit("select", {
      kind: "IMAGE",
      assetId: "square",
      width: 100,
      height: 100,
    });
    await flushPromises();
    expect(w.emitted("update:asset")).toBeUndefined();
    expect(w.text()).toContain("横向");
    w.getComponent({ name: "AssetPicker" }).vm.$emit("select", {
      kind: "IMAGE",
      assetId: "wide",
      width: 600,
      height: 200,
    });
    await flushPromises();
    expect(w.emitted("update:asset")?.[0]?.[0]).toMatchObject({
      assetId: "wide",
    });
  });
});

it("uploads wide banners, handles errors and ignores late upload on target change", async () => {
  const w = mount(CategoryImageEditor, {
    props: { asset: null, secondary: true, targetKey: "a", canUpload: true },
    global: { stubs: { AssetPicker: true } },
  });
  wrappers.push(w);
  const input = w.get("input[type=file]");
  const file = new File(["x"], "banner.png", { type: "image/png" });
  const choose = async () => {
    Object.defineProperty(input.element, "files", {
      value: [file],
      configurable: true,
    });
    await input.trigger("change");
    await flushPromises();
  };
  vi.mocked(validateMediaFile).mockResolvedValue("too large");
  await choose();
  expect(w.text()).toContain("too large");
  expect(api).not.toHaveBeenCalled();
  vi.mocked(validateMediaFile).mockResolvedValue("");
  vi.mocked(api).mockRejectedValue(new Error("network"));
  await choose();
  expect(w.text()).toContain("上传失败");
  vi.mocked(api).mockResolvedValue({
    assetId: "wide",
    kind: "IMAGE",
    width: 900,
    height: 300,
    adminUrl: "/image",
  });
  await choose();
  expect(w.emitted("update:asset")?.at(-1)?.[0]).toMatchObject({
    assetId: "wide",
  });
  await w.setProps({
    asset: {
      assetId: "wide",
      kind: "IMAGE",
      width: 900,
      height: 300,
      adminUrl: "/image",
    } as any,
  });
  await w.get("button").trigger("click");
  expect(w.emitted("update:asset")?.at(-1)?.[0]).toBeNull();
  let finish: (value: any) => void = () => {};
  vi.mocked(api).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await choose();
  const count = w.emitted("update:asset")?.length;
  await w.setProps({ targetKey: "b" });
  finish({ assetId: "late", kind: "IMAGE", width: 900, height: 300 });
  await flushPromises();
  expect(w.emitted("update:asset")?.length).toBe(count);
});
it("primary icons require square and disabled editing ignores selection", async () => {
  const w = mount(CategoryImageEditor, {
    props: { asset: null, secondary: false, targetKey: "a", canUpload: false },
    global: { stubs: { AssetPicker: true } },
  });
  wrappers.push(w);
  const picker = w.getComponent({ name: "AssetPicker" });
  picker.vm.$emit("select", { kind: "IMAGE", width: 900, height: 300 });
  await flushPromises();
  expect(w.text()).toContain("正方形");
  picker.vm.$emit("select", {
    assetId: "square",
    kind: "IMAGE",
    width: 100,
    height: 100,
  });
  await flushPromises();
  expect(w.emitted("update:asset")).toHaveLength(1);
  await w.setProps({ disabled: true });
  picker.vm.$emit("select", {
    assetId: "disabled",
    kind: "IMAGE",
    width: 100,
    height: 100,
  });
  await flushPromises();
  expect(w.emitted("update:asset")).toHaveLength(1);
});
