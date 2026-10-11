import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { createHash, webcrypto } from "node:crypto";
import ProductProfitView from "../../src/views/stores/ProductProfitView.vue";
import { api, ApiError, confirmedWrite, type Account } from "../../src/api";
vi.mock("../../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/api")>()),
  api: vi.fn(),
  confirmedWrite: vi.fn(),
}));
vi.mock("../../src/shared/confirm",()=>({confirmAction:vi.fn(async()=>true)}));
import {confirmAction} from "../../src/shared/confirm";
beforeEach(()=>vi.mocked(confirmAction).mockResolvedValue(true));
const wrappers: ReturnType<typeof mount>[] = [];
const row = {
  productId: "p1",
  productName: "礼盒",
  productNo: "P001",
  platformShareBps: 2000,
  shareRevision: 1,
  shareSource: "PRODUCT",
  skus: [
    {
      skuId: "s1",
      currentUnitVersionId: "unit-1",
      specLabel: "双瓶装",
      saleUnit: "盒",
      purchaseCostFen: 6000,
      revision: 2,
    },
  ],
};
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
async function setup() {
  vi.stubGlobal("crypto", webcrypto);
  vi.mocked(api).mockImplementation(async (url) =>
    String(url) === "/categories" ? [] : { items: [row], total: 21 },
  );
  const w = mount(ProductProfitView, {
    props: {
      account: {
        accountId: "a",
        permissionCodes: ["stores.read", "stores.manage"],
      } as Account,
    },
    global: {
      stubs: {
        ElDialog: {
          props: ["modelValue"],
          template: '<div v-if="modelValue"><slot/></div>',
        },
      },
    },
  });
  wrappers.push(w);
  await flushPromises();
  return w;
}
describe("separate product share and SKU cost", () => {
  it("cost save carries SKU revisions and does not set product share", async () => {
    const w = await setup();
    await w.get('[data-action="cost"]').trigger("click");
    await w.get('[data-cost="s1"]').setValue("61.20");
    await w.get('form[aria-label="规则编辑"]').trigger("submit");
    expect(w.find("#profit-password").exists()).toBe(false);
    vi.mocked(confirmedWrite).mockResolvedValue({});
    await w.get('form[aria-label="确认规则保存"]').trigger("submit");
    await flushPromises();
    await vi.waitFor(() => expect(confirmedWrite).toHaveBeenCalled());
    const expectedTarget = createHash("sha256")
      .update(JSON.stringify([["s1", 2, 6120, "unit-1"]]))
      .digest("hex");
    expect(confirmedWrite).toHaveBeenCalledWith(
      "stores.cost.batch.configure",
      "",
      "/stores/purchase-costs",
      "POST",
      {
        items: [
          {
            skuId: "s1",
            expectedRevision: 2,
            purchaseCostFen: 6120,
            expectedUnitVersionId: "unit-1",
          },
        ],
      },
      expectedTarget,
      0,
    );
  });
  it("product share save carries only selected product revisions", async () => {
    const w = await setup();
    await w.get('[data-action="share"]').trigger("click");
    await w.get("#product-share").setValue("25");
    await w.get('form[aria-label="规则编辑"]').trigger("submit");
    expect(w.find("#profit-password").exists()).toBe(false);
    vi.mocked(confirmedWrite).mockResolvedValue({});
    await w.get('form[aria-label="确认规则保存"]').trigger("submit");
    await flushPromises();
    await vi.waitFor(() => expect(confirmedWrite).toHaveBeenCalled());
    expect(confirmedWrite).toHaveBeenCalledWith(
      "stores.share.batch.configure",
      "",
      "/stores/platform-shares",
      "POST",
      {
        items: [{ productId: "p1", expectedRevision: 1 }],
        platformShareBps: 2500,
      },
      expect.any(String),
      0,
    );
  });
  it("clears current page selection on page changes", async () => {
    const w = await setup();
    await w.get('[data-select="p1"]').setValue(true);
    expect(w.text()).toContain("已选 1");
    await w.get('.admin-list-pager .btn-next').trigger("click");
    await flushPromises();
    expect(w.text()).toContain("已选 0");
    expect(api).toHaveBeenLastCalledWith(expect.stringContaining("page=2"));
  });
});
describe("profit rule recovery and boundaries", () => {
  it("batch cost filling and batch shares operate on current page only", async () => {
    const w = await setup();
    await w.get('[data-select="p1"]').setValue(true);
    await w
      .findAll("button")
      .find((b) => b.text() === "批量设置规格进货价")!
      .trigger("click");
    await w.get('input[aria-label="批量进货价"]').setValue("65");
    await w.get('.cost-grid input[type="checkbox"]').setValue(true);
    await w
      .findAll("button")
      .find((b) => b.text() === "填入勾选规格")!
      .trigger("click");
    expect(w.get('[data-cost="s1"]').element).toHaveProperty("value", "65");
    await w.get('.cost-grid input[type="checkbox"]').setValue(false);
    await w
      .findAll("button")
      .find((b) => b.text() === "取消")!
      .trigger("click");
    await w
      .findAll("button")
      .find((b) => b.text() === "批量设置平台比例")!
      .trigger("click");
    await flushPromises();
    await w.get("#product-share").setValue("100.01");
    expect(
      w
        .findAll("button")
        .find((b) => b.text() === "保存设置")!
        .attributes("disabled"),
    ).toBeDefined();
    await w.get("#product-share").setValue("20");
    await w.get('form[aria-label="规则编辑"]').trigger("submit");
    await w
      .findAll("button")
      .find((b) => b.text() === "取消确认")!
      .trigger("click");
    expect(w.find("#profit-password").exists()).toBe(false);
  });
  it("blocks new writes after unknown result until reloaded, clears sensitive state on revocation", async () => {
    const w = await setup();
    await w.get('[data-action="share"]').trigger("click");
    await w.get('form[aria-label="规则编辑"]').trigger("submit");
    expect(w.find("#profit-password").exists()).toBe(false);
    vi.mocked(confirmedWrite).mockRejectedValue(new Error("secret"));
    await w.get('form[aria-label="确认规则保存"]').trigger("submit");
    await flushPromises();
    await vi.waitFor(() => expect(w.text()).toContain("保存结果未确认"));
    expect(w.text()).not.toContain("secret");
    await w
      .findAll("button")
      .find((b) => b.text() === "取消")!
      .trigger("click");
    expect(w.get('[data-action="share"]').attributes("disabled")).toBeDefined();
    await w
      .findAll("button")
      .find((b) => b.text() === "重新读取")!
      .trigger("click");
    await flushPromises();
    expect(
      w.get('[data-action="share"]').attributes("disabled"),
    ).toBeUndefined();
    await w.setProps({
      account: { accountId: "a", permissionCodes: ["stores.read"] } as Account,
    });
    await flushPromises();
    expect(w.text()).toContain("只读模式");
    expect(w.get('[data-action="share"]').attributes("disabled")).toBeDefined();
    await w.setProps({
      account: { accountId: "b", permissionCodes: [] } as Account,
    });
    await flushPromises();
    expect(w.text()).not.toContain("礼盒");
  });
  it("server filtering and pagination reloads safely after an invalid response", async () => {
    const w = await setup();
    vi.mocked(api).mockResolvedValue({ items: "invalid", total: 4 });
    await w.get("input[type=search]").setValue("礼盒");
    await w.get('form[aria-label="搜索商品分润规则"]').trigger("submit");
    await flushPromises();
    expect(w.text()).toContain("读取规则失败");
    vi.mocked(api).mockResolvedValue({ items: [], total: 0 });
    await w
      .findAll("button")
      .find((b) => b.text() === "重新读取")!
      .trigger("click");
    await flushPromises();
    expect(w.text()).toContain("暂无符合条件");
  });
  it("rejects oversized multi-product SKU changes before opening and displays unconfigured costs", async () => {
    const w = await setup();
    vi.mocked(api).mockResolvedValue({
      items: [
        {
          ...row,
          shareSource: "LEGACY_CONFLICT",
          platformShareBps: null,
          skus: Array.from({ length: 101 }, (_, i) => ({
            ...row.skus[0],
            skuId: `s${i}`,
            specLabel: "",
            purchaseCostFen: null,
            requiresReconfiguration: true,
          })),
        },
      ],
      total: 1,
    });
    await w
      .findAll("button")
      .find((b) => b.text() === "重新读取")!
      .trigger("click");
    await flushPromises();
    expect(w.text()).toContain("待统一确认");
    await w.get('[data-action="cost"]').trigger("click");
    expect(w.text()).toContain("最多设置 100");
    expect(w.find('[data-cost="s0"]').exists()).toBe(false);
  });
});
it("requires fresh unit version after a rejected cost update before another confirmation", async () => {
  const w = await setup();
  await w.get('[data-action="cost"]').trigger("click");
  await w.get('[data-cost="s1"]').setValue("60");
  await w.get('form[aria-label="规则编辑"]').trigger("submit");
  expect(w.find("#profit-password").exists()).toBe(false);
  vi.mocked(confirmedWrite).mockRejectedValue(
    new ApiError("销售单位已变化", 409, "UNIT_REVISION_CONFLICT"),
  );
  await w.get('form[aria-label="确认规则保存"]').trigger("submit");
  await vi.waitFor(() =>
    expect(w.text()).toContain("本次未保存。请重新读取并按新单位核对进货价"),
  );
  expect(w.get('[data-cost="s1"]').element).toHaveProperty("value", "60");
  await w
    .findAll("button")
    .find((b) => b.text() === "取消")!
    .trigger("click");
  expect(w.get('[data-action="cost"]').attributes("disabled")).toBeDefined();
  vi.mocked(api).mockResolvedValue({
    items: [
      { ...row, skus: [{ ...row.skus[0], currentUnitVersionId: "unit-2" }] },
    ],
    total: 1,
  });
  await w
    .findAll("button")
    .find((b) => b.text() === "重新读取")!
    .trigger("click");
  await flushPromises();
  await w.get('[data-action="cost"]').trigger("click");
  await w.get('form[aria-label="规则编辑"]').trigger("submit");
  expect(w.find("#profit-password").exists()).toBe(false);
  vi.mocked(confirmedWrite).mockResolvedValue({});
  await w.get('form[aria-label="确认规则保存"]').trigger("submit");
  await vi.waitFor(() => expect(confirmedWrite).toHaveBeenCalledTimes(2));
  expect(vi.mocked(confirmedWrite).mock.lastCall?.[4]).toEqual({
    items: [
      {
        skuId: "s1",
        expectedRevision: 2,
        purchaseCostFen: 6000,
        expectedUnitVersionId: "unit-2",
      },
    ],
  });
});
it("does not submit purchase costs when the current sales unit is missing", async () => {
  const w = await setup();
  vi.mocked(api).mockResolvedValue({
    items: [{ ...row, skus: [{ ...row.skus[0], currentUnitVersionId: null }] }],
    total: 1,
  });
  await w
    .findAll("button")
    .find((b) => b.text() === "重新读取")!
    .trigger("click");
  await flushPromises();
  await w.get('[data-action="cost"]').trigger("click");
  expect(w.text()).toContain("请先在商品管理配置销售单位");
  expect(
    w
      .findAll("button")
      .find((b) => b.text() === "保存设置")!
      .attributes("disabled"),
  ).toBeDefined();
  expect(confirmedWrite).not.toHaveBeenCalled();
});

it('resets product profit filters to the complete server query',async()=>{
 const w=await setup();await w.get('input[type="search"]').setValue('测试商品');
 const reset=w.findAll('button').find(b=>b.text()==='重置');expect(reset).toBeDefined();await reset!.trigger('click');await flushPromises();
 expect(vi.mocked(api).mock.lastCall?.[0]).toContain('search=&categoryId=&configuration=ALL&page=1&pageSize=20');expect(w.get('input[type="search"]').element).toHaveProperty('value','')
})
it('permits current-page read-only comparison without allowing rule writes',async()=>{
 const w=await setup();await w.setProps({account:{accountId:'reader',permissionCodes:['stores.read']} as Account});await flushPromises();
 expect(w.get('[data-select="p1"]').attributes('disabled')).toBeUndefined();await w.get('[data-select="p1"]').setValue(true);const compare=w.findAll('button').find(b=>b.text()==='对比所选规则');expect(compare).toBeDefined();await compare!.trigger('click');await flushPromises();
 expect(w.text()).toContain('当前页所选 1 条记录');expect(w.text()).toContain('双瓶装');expect(w.get('[data-action="cost"]').attributes('disabled')).toBeDefined();expect(vi.mocked(api).mock.calls.some(c=>c[1]?.method)).toBe(false)
})
