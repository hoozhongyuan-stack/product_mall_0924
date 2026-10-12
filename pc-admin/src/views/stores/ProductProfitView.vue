<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElDialog } from "element-plus";
import { api, ApiError, confirmedWrite, type Account } from "../../api";
import { amount } from "./types";
import "./stores.css";
import {confirmAction} from "../../shared/confirm";
import {onBeforeRouteLeave} from "vue-router";
import AdminSelectedRowsDialog from "../../shared/list/AdminSelectedRowsDialog.vue";
import AdminListPager from "../../shared/list/AdminListPager.vue";
import AdminSelectionBar from "../../shared/list/AdminSelectionBar.vue";
interface Sku {
  skuId: string;
  currentUnitVersionId: string | null;
  specLabel?: string;
  skuCode?: string;
  saleUnit: string;
  purchaseCostFen: number | null;
  revision: number;
  requiresReconfiguration?: boolean;
}
interface Product {
  productId: string;
  productName: string;
  productNo: string;
  platformShareBps: number | null;
  shareRevision: number;
  shareSource: string;
  skus: Sku[];
}
const props = defineProps<{ account: Account }>();
const rows = ref<Product[]>([]),
  total = ref(0),
  page = ref(1),
  pageSize = ref(20),
  search = ref(""),
  categoryId = ref(""),
  configuration = ref("ALL");
const categories = ref<{ id: string; name: string }[]>([]),
  checked = ref<string[]>([]),
  editing = ref<Product[]>([]);
const mode = ref<"cost" | "share" | null>(null),
  costs = ref<Record<string, string>>({}),
  share = ref(""),
  costChecked = ref<string[]>([]),
  batchCost = ref("");
const loading = ref(false),
  busy = ref(false),
  needsRead = ref(false),
  confirming = ref(false),
  error = ref(""),
  notice = ref("");
const readable = computed(() =>
  props.account.permissionCodes.includes("stores.read"),
);
const permitted = computed(() =>
  props.account.permissionCodes.includes("stores.manage"),
);
const skus = computed(() =>
  editing.value.flatMap((row) =>
    row.skus.map((sku) => ({ ...sku, productName: row.productName })),
  ),
);
let epoch = 0;
let editBaseline='';
const draftSignature=()=>JSON.stringify([share.value,costs.value]);
const dirty=computed(()=>!!mode.value&&draftSignature()!==editBaseline);
async function closeEdit(done?:()=>void){if(busy.value)return false;if(dirty.value&&!await confirmAction('进货价与分润尚未保存，确定关闭吗？'))return false;reset();done?.();return true}
onBeforeRouteLeave(()=>closeEdit());
function hundredths(value: string, max: number) {
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return null;
  const [a, b = ""] = value.split(".");
  const n = Number(a) * 100 + Number(b.padEnd(2, "0"));
  return Number.isSafeInteger(n) && n <= max ? n : null;
}
const allowed = computed(
  () =>
    permitted.value &&
    !busy.value &&
    !loading.value &&
    !needsRead.value &&
    editing.value.length > 0 &&
    (mode.value === "share"
      ? hundredths(share.value, 10000) !== null
      : skus.value.length > 0 &&
        skus.value.every(
          (s) =>
            !!s.currentUnitVersionId &&
            hundredths(costs.value[s.skuId] || "", 9900000000) !== null,
        )),
);
const comparison=ref(false);
const comparisonRows=computed(()=>rows.value.filter(row=>checked.value.includes(row.productId)).map(row=>({product:row.productName,code:row.productNo,costs:row.skus.map(sku=>`${sku.specLabel||sku.skuCode||'默认规格'} / 每${sku.saleUnit}：${sku.requiresReconfiguration?'需重新配置':sku.purchaseCostFen===null?'未配置':amount(sku.purchaseCostFen)}`).join('；'),share:row.platformShareBps===null?'未配置':`${row.platformShareBps/100}%`})));
const selectionBlocked=computed(()=>!readable.value||busy.value||loading.value||needsRead.value||!!mode.value);
function resetQuery(){if(busy.value||loading.value||mode.value)return;search.value='';categoryId.value='';configuration.value='ALL';page.value=1;void load()}
const blocked = computed(
  () =>
    !permitted.value ||
    busy.value ||
    loading.value ||
    needsRead.value ||
    !!mode.value,
);
function reset() {
  mode.value = null;
  editing.value = [];

  confirming.value = false;
  costChecked.value = [];
  batchCost.value = "";
  costs.value = {};
}
async function load() {
  if (!readable.value || busy.value) return;
  if(mode.value&&!await closeEdit())return;
  const token = ++epoch;
  loading.value = true;
  error.value = "";
  checked.value = [];
  comparison.value=false;
  reset();
  try {
    const result = await api<{ items: Product[]; total: number }>(
      `/stores/product-profit-rules?search=${encodeURIComponent(search.value.trim())}&categoryId=${encodeURIComponent(categoryId.value)}&configuration=${configuration.value}&page=${page.value}&pageSize=${pageSize.value}`,
    );
    if (token !== epoch) return;
    if (!Array.isArray(result.items) || !Number.isInteger(result.total))
      throw new Error();
    rows.value = result.items;
    total.value = result.total;
    needsRead.value = false;
  } catch {
    if (token === epoch) {
      rows.value = [];
      error.value = "读取规则失败，请重新读取。";
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
function open(kind: "cost" | "share", products: Product[]) {
  if (blocked.value || !permitted.value || !products.length) return;
  if (
    kind === "cost" &&
    products.reduce((n, p) => n + p.skus.length, 0) > 100
  ) {
    error.value = "一次最多设置 100 个规格，请减少勾选商品后重试。";
    return;
  }
  editing.value = products.map((p) => ({
    ...p,
    skus: p.skus.map((s) => ({ ...s })),
  }));
  mode.value = kind;
  share.value =
    products.length === 1 && products[0]?.platformShareBps !== null
      ? String(products[0]!.platformShareBps! / 100)
      : "";
  costs.value = Object.fromEntries(
    products.flatMap((p) =>
      p.skus.map((s) => [
        s.skuId,
        s.requiresReconfiguration || s.purchaseCostFen === null
          ? ""
          : (s.purchaseCostFen / 100).toFixed(2),
      ]),
    ),
  );
  editBaseline=draftSignature();
  error.value = "";
  notice.value = "";
}
function openBatch(kind: "cost" | "share") {
  open(
    kind,
    rows.value.filter((row) => checked.value.includes(row.productId)),
  );
}
function select(id: string, value: boolean) {
  checked.value = value
    ? [...checked.value, id]
    : checked.value.filter((x) => x !== id);
}
function costSelect(id: string, value: boolean) {
  costChecked.value = value
    ? [...costChecked.value, id]
    : costChecked.value.filter((x) => x !== id);
}
function applyBatchCost() {
  if (
    confirming.value ||
    busy.value ||
    hundredths(batchCost.value, 9900000000) === null
  )
    return;
  costs.value = {
    ...costs.value,
    ...Object.fromEntries(costChecked.value.map((id) => [id, batchCost.value])),
  };
}
async function target(value: unknown) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(value)),
  );
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
async function save() {
  if (!allowed.value || !confirming.value ) return;
  const token = ++epoch,
    kind = mode.value,
    secret = '';
  busy.value = true;
  error.value = "";
  try {
    if (kind === "cost") {
      const items = skus.value
        .map((s) => ({
          skuId: s.skuId.toLowerCase(),
          expectedRevision: s.revision,
          expectedUnitVersionId: s.currentUnitVersionId,
          purchaseCostFen: hundredths(costs.value[s.skuId]!, 9900000000)!,
        }))
        .sort((a, b) => a.skuId.localeCompare(b.skuId));
      const hash = await target(
        items.map((s) => [
          s.skuId,
          s.expectedRevision,
          s.purchaseCostFen,
          s.expectedUnitVersionId,
        ]),
      );
      if (token !== epoch) return;
      await confirmedWrite(
        "stores.cost.batch.configure",
        secret,
        "/stores/purchase-costs",
        "POST",
        { items },
        hash,
        0,
      );
    } else {
      const items = editing.value
        .map((p) => ({
          productId: p.productId.toLowerCase(),
          expectedRevision: p.shareRevision,
        }))
        .sort((a, b) => a.productId.localeCompare(b.productId));
      const platformShareBps = hundredths(share.value, 10000)!;
      const hash = await target([
        platformShareBps,
        items.map((p) => [p.productId, p.expectedRevision]),
      ]);
      if (token !== epoch) return;
      await confirmedWrite(
        "stores.share.batch.configure",
        secret,
        "/stores/platform-shares",
        "POST",
        { items, platformShareBps },
        hash,
        0,
      );
    }
    if (token === epoch) {
      busy.value = false;
      reset();
      await load();
      notice.value =
        kind === "cost"
          ? "规格进货价已保存，平台比例保持不变。"
          : "商品平台比例已保存，规格进货价保持不变。";
    }
  } catch (reason) {
    if (token === epoch) {
      needsRead.value = true;
      error.value =
        reason instanceof ApiError && reason.code === "UNIT_REVISION_CONFLICT"
          ? "规格销售单位已变更，本次未保存。请重新读取并按新单位核对进货价。"
          : "保存结果未确认，请关闭弹窗，重新读取并核对后再操作。";
    }
  } finally {
    if (token === epoch) {
      busy.value = false;

      confirming.value = false;
    }
  }
}
function changePage(next: number,size=pageSize.value) {
  pageSize.value=size;
  page.value = next;
  void load();
}
watch(
  () =>
    JSON.stringify([props.account.accountId, props.account.permissionCodes]),
  () => {
    epoch++;
    busy.value = false;
    loading.value = false;
    rows.value = [];
    checked.value = [];
    reset();
    void load();
  },
);
onMounted(() => {
  void load();
  if (props.account.permissionCodes.includes("catalog.read"))
    void api<{ id: string; name: string }[]>("/categories")
      .then((result) => {
        categories.value = result;
      })
      .catch(() => {
        notice.value = "分类筛选暂不可用，可使用名称或编码检索。";
      });
});
onUnmounted(() => {
  epoch++;
  reset();
});
</script>
<template>
  <section class="page-content stores-page admin-list-page">
    <header class="page-heading">
      <div>
        <h1>商品进货价与分润</h1>
        <p>
          进货价按规格设置，平台分成比例按商品设置。已成交订单保留原结算快照。
        </p>
      </div>
      <button
        class="secondary-button"
        :disabled="busy || loading"
        @click="load"
      >
        重新读取
      </button>
    </header>
    <p v-if="!readable" role="alert">当前账号没有规则读取权限。</p>
    <p v-else-if="!permitted" role="status">当前为只读模式，不能修改规则。</p>
    <section v-if="readable" class="panel store-section">
      <form
        class="store-search"
        aria-label="搜索商品分润规则"
        @submit.prevent="
          page = 1;
          load();
        "
      >
        <label
          >商品名称 / 编码<input
            v-model="search"
            type="search"
            maxlength="100"
            :disabled="busy || loading || !!mode" /></label
        ><label
          >分类<select
            v-model="categoryId"
            :disabled="busy || loading || !!mode"
          >
            <option value="">全部分类</option>
            <option v-for="c in categories" :key="c.id" :value="c.id">
              {{ c.name }}
            </option>
          </select></label
        ><label
          >配置状态<select
            v-model="configuration"
            :disabled="busy || loading || !!mode"
          >
            <option value="ALL">全部状态</option>
            <option value="CONFIGURED">已配置</option>
            <option value="UNCONFIGURED">未配置</option>
            <option value="NEEDS_CONFIRMATION">待统一确认</option>
          </select></label
        ><button class="primary-button" :disabled="busy || loading || !!mode">
          查询
        </button><button class="secondary-button" type="button" :disabled="busy||loading||!!mode" @click="resetQuery">重置</button>
      </form>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="loading" role="status">正在读取商品规则…</p>
      <p v-else-if="!rows.length && !error" class="empty-state">
        暂无符合条件的商品。
      </p>
      <AdminSelectionBar :count="checked.length" unit="个商品" :loading="busy||loading" @clear="checked=[]"><button
          class="secondary-button"
          :disabled="blocked || !checked.length"
          @click="openBatch('cost')"
        >
          批量设置规格进货价</button
        ><button
          class="secondary-button"
          :disabled="blocked || !checked.length"
          @click="openBatch('share')"
        >
          批量设置平台比例
        </button><button class="secondary-button" :disabled="selectionBlocked||!checked.length" @click="comparison=true">对比所选规则</button>
      </AdminSelectionBar>
      <div
        v-if="rows.length"
        class="table-wrap admin-list-table"
        tabindex="0"
        aria-label="商品分润列表，可横向滚动"
      >
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="选择当前页全部商品"
                  :disabled="selectionBlocked"
                  :checked="checked.length === rows.length"
                  :indeterminate="
                    checked.length > 0 && checked.length < rows.length
                  "
                  @change="
                    checked = ($event.target as HTMLInputElement).checked
                      ? rows.map((p) => p.productId)
                      : []
                  "
                />
              </th>
              <th>商品</th>
              <th>规格进货价</th>
              <th>平台分成比例</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.productId">
              <td>
                <input
                  type="checkbox"
                  :data-select="row.productId"
                  :aria-label="`选择 ${row.productName}`"
                  :checked="checked.includes(row.productId)"
                  :disabled="selectionBlocked"
                  @change="
                    select(
                      row.productId,
                      ($event.target as HTMLInputElement).checked,
                    )
                  "
                />
              </td>
              <td>
                {{ row.productName }}<small>{{ row.productNo }}</small>
              </td>
              <td>
                {{ row.skus.length }} 个规格<small
                  >已配置
                  {{
                    row.skus.filter(
                      (s) =>
                        s.purchaseCostFen !== null &&
                        !s.requiresReconfiguration,
                    ).length
                  }}
                  个</small
                >
              </td>
              <td>
                {{
                  row.shareSource === "LEGACY_CONFLICT"
                    ? "待统一确认"
                    : row.platformShareBps === null
                      ? "未配置"
                      : `${row.platformShareBps / 100}%`
                }}<small v-if="row.shareSource === 'LEGACY_UNIFORM'"
                  >原规格比例一致，待商品确认</small
                >
              </td>
              <td>
                <button
                  class="text-button"
                  data-action="cost"
                  :disabled="blocked"
                  @click="open('cost', [row])"
                >
                  设置规格进货价</button
                ><button
                  class="text-button"
                  data-action="share"
                  :disabled="blocked"
                  @click="open('share', [row])"
                >
                  设置平台比例
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminListPager v-if="!error" :page="page" :page-size="pageSize" :total="total" :loading="busy||loading||!!mode" @change="changePage"/>
    </section>
    <ElDialog
      :model-value="!!mode"
      :title="mode === 'cost' ? '设置规格进货价' : '设置商品平台比例'"
      width="min(800px,94vw)"
      :close-on-click-modal="false"
      :show-close="!busy"
      :close-on-press-escape="!busy"
      :before-close="closeEdit"
      ><p v-if="error" role="alert">{{ error }}</p>
      <form
        aria-label="规则编辑"
        @submit.prevent="allowed && (confirming = true)"
      >
        <p>本次设置 {{ editing.length }} 个商品，仅影响后续订单。</p>
        <template v-if="mode === 'cost'"
          ><p>
            按每个规格的销售单位填写，瓶装与箱装分别设置。商品平台比例保持不变。
          </p>
          <div class="store-actions">
            <input
              v-model="batchCost"
              aria-label="批量进货价"
              inputmode="decimal"
              :disabled="busy || confirming"
            /><button
              type="button"
              class="secondary-button"
              :disabled="
                busy ||
                confirming ||
                !costChecked.length ||
                hundredths(batchCost, 9900000000) === null
              "
              @click="applyBatchCost"
            >
              填入勾选规格
            </button>
          </div>
          <div class="table-wrap cost-grid">
            <table>
              <thead>
                <tr>
                  <th>勾选</th>
                  <th>商品 / 规格</th>
                  <th>当前进货价</th>
                  <th>新进货价（元）</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in skus" :key="s.skuId">
                  <td>
                    <input
                      type="checkbox"
                      :disabled="busy || confirming"
                      :checked="costChecked.includes(s.skuId)"
                      @change="
                        costSelect(
                          s.skuId,
                          ($event.target as HTMLInputElement).checked,
                        )
                      "
                    />
                  </td>
                  <td>
                    {{ s.productName
                    }}<small
                      >{{ s.specLabel || "默认规格" }} · 每{{
                        s.saleUnit
                      }}</small
                    ><small v-if="!s.currentUnitVersionId"
                      >请先在商品管理配置销售单位，再设置进货价。</small
                    ><small v-if="s.requiresReconfiguration"
                      >销售单位变化，需重新配置</small
                    >
                  </td>
                  <td>
                    {{
                      s.requiresReconfiguration
                        ? "需重新配置"
                        : s.purchaseCostFen === null
                          ? "未配置"
                          : amount(s.purchaseCostFen)
                    }}
                  </td>
                  <td>
                    <input
                      v-model="costs[s.skuId]"
                      :data-cost="s.skuId"
                      :aria-label="`${s.specLabel || '默认规格'}进货价`"
                      inputmode="decimal"
                      maxlength="16"
                      :disabled="busy || confirming"
                    />
                  </td>
                </tr>
              </tbody>
            </table></div></template
        ><template v-else
          ><ul>
            <li v-for="p in editing" :key="p.productId">{{ p.productName }}</li>
          </ul>
          <label for="product-share"
            >平台分成比例（%）<input
              id="product-share"
              v-model="share"
              inputmode="decimal"
              maxlength="6"
              :disabled="busy || confirming"
          /></label>
          <p>
            0–100%，最多两位小数；同一商品全部规格共用此比例，规格进货价保持不变。
          </p></template
        >
        <div class="store-actions">
          <button class="primary-button" :disabled="!allowed || confirming">
            保存设置</button
          ><button
            class="secondary-button"
            type="button"
            :disabled="busy"
            @click="closeEdit()"
          >
            取消
          </button>
        </div>
      </form>
      <form v-if="confirming" aria-label="确认规则保存" @submit.prevent="save">
        <h3>确认保存</h3>
        <p>已有订单保留原结算快照。请核对本次修改后确认保存。</p>
        <button class="primary-button" :disabled="busy ">
          {{ busy ? "正在保存…" : "确认保存" }}</button
        ><button
          type="button"
          class="text-button"
          :disabled="busy"
          @click="
            confirming = false;
          "
        >
          取消确认
        </button>
      </form></ElDialog
    >
    <AdminSelectedRowsDialog v-model="comparison" title="商品进货价与分润对比" :rows="comparisonRows" :columns="[{key:'product',label:'商品'},{key:'code',label:'商品编码'},{key:'costs',label:'各规格进货价'},{key:'share',label:'商品平台比例'}]"/>
  </section>
</template>
<style scoped>
.store-rule-toolbar {
  margin: 18px 0;
  flex-wrap: wrap;
}
.cost-grid {
  max-height: 400px;
  overflow: auto;
  margin: 18px 0;
}
.cost-grid input:not([type="checkbox"]) {
  min-width: 100px;
  width: 130px;
}
.store-pagination {
  flex-wrap: wrap;
}
select {
  padding: 8px;
}
form[aria-label="确认规则保存"] {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--mall-color-border);
}
</style>
