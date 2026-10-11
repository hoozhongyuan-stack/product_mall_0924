<script setup lang="ts">
import AdminListPager from '../../shared/list/AdminListPager.vue';
import AdminSelectionBar from '../../shared/list/AdminSelectionBar.vue';
import AdminSelectedRowsDialog from '../../shared/list/AdminSelectedRowsDialog.vue';
import AdminStatusBadge from '../../shared/list/AdminStatusBadge.vue';
import { pageWindow } from '../../shared/list/list-state.mjs';
import { useCurrentPageSelection } from '../../shared/list/useCurrentPageSelection';
import { confirmAction } from "../../shared/confirm";

import { computed, onMounted, onUnmounted, watch, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { ElDialog, ElMessage } from "element-plus";
import { ApiError, api, type Account } from "../../api";
import CategoryImageEditor from "./CategoryImageEditor.vue";
import type { Asset, Category, CategoryStatus } from "./types";

const props = defineProps<{ account: Account }>();
const canWrite = computed(() =>
  props.account.permissionCodes.includes("catalog.write"),
);
const categories = ref<Category[]>([]);
const roots = computed(() =>
  categories.value.filter((item) => !item.parentId).sort(byOrder),
);

const keyword = ref("");
const statusFilter = ref("");
const page = ref(1);
const pageSize = ref(20);
const compareOpen = ref(false);
const appliedFilters = ref({ keyword: "", status: "" });
const filteredGroups = computed(() => {
  const query = appliedFilters.value.keyword.trim().toLocaleLowerCase();
  const nameMatches = (row: Category) =>
    !query || row.name.toLocaleLowerCase().includes(query);
  const statusMatches = (row: Category) =>
    !appliedFilters.value.status || row.status === appliedFilters.value.status;
  return roots.value.flatMap((root) => {
    const children = categories.value
      .filter((row) => row.parentId === root.id)
      .sort(byOrder);
    const matchingChildren = children.filter(
      (row) => statusMatches(row) && (nameMatches(row) || nameMatches(root)),
    );
    const rootMatches = nameMatches(root) && statusMatches(root);
    return rootMatches || matchingChildren.length
      ? [{ root, children: matchingChildren }]
      : [];
  });
});
const rootPage = computed(() =>
  pageWindow(filteredGroups.value, page.value, pageSize.value),
);
const rows = computed(() =>
  rootPage.value.items.flatMap((group) => [group.root, ...group.children]),
);
const selection = useCurrentPageSelection(() => rows.value, (row) => row.id);
const comparisonColumns = [
  { key: "name", label: "分类名称" },
  { key: "level", label: "层级" },
  { key: "parent", label: "上级分类" },
  { key: "products", label: "关联商品" },
  { key: "sort", label: "排序" },
  { key: "status", label: "状态" },
];
const comparisonRows = computed(() =>
  selection.selectedRows.value.map((row) => ({
    name: row.name,
    level: row.parentId ? "二级" : "一级",
    parent: row.parentId
      ? categories.value.find((parent) => parent.id === row.parentId)?.name || "未知上级"
      : "—",
    products: row.productCount ?? 0,
    sort: row.sortOrder,
    status: row.status === "ACTIVE" ? "启用" : "停用",
  })),
);
function applyFilters(reset = false) {
  if (reset) {
    keyword.value = "";
    statusFilter.value = "";
  }
  appliedFilters.value = { keyword: keyword.value, status: statusFilter.value };
  page.value = 1;
  selection.clear();
}
function changePage(next: number, size: number) {
  page.value = next;
  pageSize.value = size;
  selection.clear();
}
watch([keyword, statusFilter], selection.clear);

const loading = ref(true);
const saving = ref(false);
const error = ref("");
const listError = ref("");
const needsRead = ref(false);
const savedEditorSnapshot = ref("");
const editorSnapshot = () =>
  JSON.stringify([
    name.value,
    parentId.value,
    sortOrder.value,
    status.value,
    image.value?.assetId || null,
  ]);
const dirty = computed(
  () => !!editor.value && editorSnapshot() !== savedEditorSnapshot.value,
);
const dialogTitle = computed(() =>
  editor.value === "new"
    ? parentId.value
      ? "添加二级分类"
      : "新建一级分类"
    : editor.value
      ? `编辑分类 · ${editor.value.name}`
      : "分类设置",
);
const editor = ref<Category | "new" | null>(null);
const name = ref("");
const parentId = ref("");
const sortOrder = ref(0);
const image = ref<Asset | null>(null),
  mediaBusy = ref(false);
const canUpload = computed(() =>
  props.account.permissionCodes.includes("asset.upload"),
);
const secondary = computed(() =>
  editor.value === "new"
    ? !!parentId.value
    : !!(editor.value && editor.value.parentId),
);
const status = ref<CategoryStatus>("ACTIVE");
let generation = 0;
let editorSession = 0;
watch(
  () =>
    JSON.stringify([props.account.accountId, props.account.permissionCodes]),
  () => {
    generation++;
    editorSession++;
    editor.value = null;
    image.value = null;
    mediaBusy.value = false;
    saving.value = false;
    categories.value = [];
    selection.clear();
    compareOpen.value = false;
    page.value = 1;
    void load();
  },
);
onUnmounted(() => {
  generation++;
  editorSession++;
});
watch(
  parentId,
  () => {
    image.value = null;
  },
  { flush: "sync" },
);
function byOrder(a: Category, b: Category) {
  return a.sortOrder - b.sortOrder || a.name.localeCompare(b.name);
}

async function load() {
  const token = ++generation;
  loading.value = true;
  selection.clear();
  listError.value = "";
  try {
    const result = await api<Category[]>("/categories");
    if (token === generation) {
      categories.value = result;
      return true;
    }
    return false;
  } catch (reason) {
    if (token === generation) {
      listError.value =
        reason instanceof Error ? reason.message : "分类加载失败。";
    }
    return false;
  } finally {
    if (token === generation) loading.value = false;
  }
}
onMounted(load);

async function closeEditor(force = false) {
  if ((saving.value || mediaBusy.value) && !force) return false;
  const target = editor.value,
    session = editorSession;
  if (
    !force &&
    dirty.value &&
    !(await confirmAction("分类修改尚未保存，确定放弃并关闭弹窗吗？"))
  )
    return false;
  if (
    !force &&
    (saving.value ||
      mediaBusy.value ||
      editor.value !== target ||
      session !== editorSession)
  )
    return false;
  editorSession++;
  editor.value = null;
  image.value = null;
  mediaBusy.value = false;
  name.value = "";
  parentId.value = "";
  sortOrder.value = 0;
  status.value = "ACTIVE";
  needsRead.value = false;
  error.value = "";
  savedEditorSnapshot.value = "";
  return true;
}
async function beforeClose(done:()=>void){ if(await closeEditor())done(); }
onBeforeRouteLeave(() => (editor.value ? closeEditor() : true));

function openCreate(parent = "") {
  if (
    !canWrite.value ||
    saving.value ||
    loading.value ||
    listError.value ||
    editor.value
  )
    return;
  needsRead.value = false;
  editorSession++;
  image.value = null;
  editor.value = "new";
  name.value = "";
  parentId.value = parent;
  sortOrder.value = 0;
  status.value = "ACTIVE";
  error.value = "";
  savedEditorSnapshot.value = editorSnapshot();
}

function openEdit(item: Category) {
  if (
    !canWrite.value ||
    saving.value ||
    loading.value ||
    listError.value ||
    editor.value
  )
    return;
  needsRead.value = false;
  editorSession++;
  editor.value = item;
  name.value = item.name;
  parentId.value = item.parentId || "";
  image.value = item.parentId ? item.banner || null : item.image || null;
  sortOrder.value = item.sortOrder;
  status.value = item.status;
  error.value = "";
  savedEditorSnapshot.value = editorSnapshot();
}

async function save() {
  const target = editor.value;
  const session = editorSession;
  if (
    !target ||
    saving.value ||
    mediaBusy.value ||
    needsRead.value ||
    !canWrite.value
  )
    return;
  if (
    !name.value.trim() ||
    !Number.isInteger(sortOrder.value) ||
    sortOrder.value < 0
  ) {
    error.value = "请填写分类名称和非负整数排序值。";
    return;
  }
  if (
    target !== "new" &&
    target.status === "ACTIVE" &&
    status.value === "INACTIVE"
  ) {
    if (target.onSaleProductCount) {
      error.value = `该分类影响 ${target.onSaleProductCount} 个在售商品，请先下架后再停用。`;
      return;
    }
    if (
      target.productCount &&
      !(await confirmAction(
        `停用后，该分类及下级关联的 ${target.productCount} 个商品将不再展示。确定停用吗？`,
      ))
    )
      return;
  }
  if (
    saving.value ||
    mediaBusy.value ||
    editor.value !== target ||
    session !== editorSession ||
    !canWrite.value
  )
    return;
  const token = generation;
  saving.value = true;
  error.value = "";
  try {
    if (target === "new") {
      await api<Category>("/categories", {
        method: "POST",
        body: JSON.stringify({
          parentId: parentId.value || null,
          name: name.value.trim(),
          sortOrder: sortOrder.value,
          imageAssetId: secondary.value ? null : image.value?.assetId || null,
          bannerAssetId: secondary.value ? image.value?.assetId || null : null,
        }),
      });
    } else {
      await api<Category>(`/categories/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.value.trim(),
          sortOrder: sortOrder.value,
          imageAssetId: secondary.value ? null : image.value?.assetId || null,
          bannerAssetId: secondary.value ? image.value?.assetId || null : null,
          status: status.value,
          expectedRevision: target.revision,
        }),
      });
    }
    if (token !== generation) return;
    saving.value = false;
    await closeEditor(true);
    ElMessage.success("分类已保存");
    await load();
  } catch (reason) {
    if (token !== generation) return;
    if (reason instanceof ApiError && reason.code === "REVISION_CONFLICT") {
      saving.value = false;
      needsRead.value = true;
      const refreshed = await load();
      if (editor.value === target)
        error.value = refreshed
          ? "分类已被其他人修改。列表已刷新，请关闭弹窗重新打开后核对。"
          : "分类已被其他人修改，读取最新分类失败。请关闭弹窗后重新加载。";
    } else {
      error.value = reason instanceof Error ? reason.message : "分类保存失败。";
    }
  } finally {
    if (token === generation) saving.value = false;
  }
}
</script>

<template>
  <div class="catalog-section admin-list-page">
    <div class="page-heading">
      <div>
        <h2>商品分类</h2>
        <p>最多两级，支持调整同级顺序。商品绑定启用的二级分类。</p>
      </div>
      <button
        v-if="canWrite"
        class="primary-button"
        type="button"
        :disabled="saving || loading || !!listError || !!editor"
        @click="openCreate()"
      >
        新建一级分类
      </button>
    </div>
    <p v-if="listError && !editor" class="error notice" role="alert">
      {{ listError }}
    </p>
    <form class="catalog-filters" role="search" @submit.prevent="applyFilters()"><label>分类名称<input v-model="keyword" aria-label="分类关键词" maxlength="80" placeholder="一级或二级分类名称" /></label><label>分类状态<select v-model="statusFilter" aria-label="分类状态"><option value="">全部状态</option><option value="ACTIVE">启用</option><option value="INACTIVE">停用</option></select></label><button class="primary-button" type="submit" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="applyFilters(true)">重置</button></form>
    <p class="hint">按一级分类分页，每组携带符合条件的二级分类；匹配二级分类时保留上级分类，便于核对层级。</p>
    <AdminSelectionBar v-if="!loading && !listError" :count="selection.selectedIds.value.length" unit="个分类" :loading="loading || saving" @clear="selection.clear"><button class="secondary-button" :disabled="!selection.selectedIds.value.length || loading || saving" @click="compareOpen=true">核对所选分类</button></AdminSelectionBar>
    <p v-if="loading" class="loading-inline" role="status">正在加载分类…</p>
    <div v-else class="panel table-wrap category-table admin-list-table" tabindex="0" aria-label="分类列表，可横向滚动">
      <table>
        <thead>
          <tr>
            <th scope="col"><input type="checkbox" aria-label="选择当前页分类" :checked="selection.allSelected.value" :indeterminate="selection.indeterminate.value" :disabled="loading || saving || !!listError" @change="selection.togglePage(($event.target as HTMLInputElement).checked)" /></th>
            <th scope="col">分类名称</th>
            <th scope="col">层级</th>
            <th scope="col">关联商品</th>
            <th scope="col">排序</th>
            <th scope="col">状态</th>
            <th v-if="canWrite" scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in rows" :key="item.id">
            <td><input type="checkbox" :aria-label="`选择分类 ${item.name}`" :checked="selection.selectedIds.value.includes(item.id)" :disabled="loading || saving || !!listError" @change="selection.toggle(item.id,($event.target as HTMLInputElement).checked)" /></td>
            <td :class="['strong-cell', { 'child-category': !!item.parentId }]">
              {{ item.name }}
            </td>
            <td>{{ item.parentId ? "二级" : "一级" }}</td>
            <td>{{ item.productCount ?? 0 }}</td>
            <td>{{ item.sortOrder }}</td>
            <td>
              <AdminStatusBadge :status="item.status" :label="item.status === 'ACTIVE' ? '启用' : '停用'" />
            </td>
            <td v-if="canWrite">
              <button
                class="text-button"
                type="button"
                :disabled="saving || loading || !!listError || !!editor"
                @click="openEdit(item)"
              >
                编辑
              </button>
              <button
                v-if="!item.parentId && item.status === 'ACTIVE'"
                class="text-button"
                type="button"
                :disabled="saving || loading || !!listError || !!editor"
                @click="openCreate(item.id)"
              >
                添加二级
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="empty-state">
        {{categories.length?'未找到符合条件的分类，可调整检索条件。':'尚无分类。先创建一级分类，再添加二级分类，商品才能选择分类。'}}
      </p>
    </div>
    <p v-if="!loading && !listError" class="hint">共 {{filteredGroups.length}} 组一级分类，本页 {{rootPage.items.length}} 组，展示 {{rows.length}} 条分类。</p>
    <AdminListPager v-if="!loading && !listError" :page="rootPage.page" :page-size="pageSize" :total="rootPage.total" :loading="loading || saving" @change="changePage" />
    <AdminSelectedRowsDialog v-model="compareOpen" title="核对所选分类" :columns="comparisonColumns" :rows="comparisonRows" />
    <button
      v-if="listError && !loading && !editor"
      class="text-button retry"
      type="button"
      @click="load"
    >
      重新加载
    </button>
    <ElDialog
      :model-value="!!editor"
      :title="dialogTitle"
      width="min(640px,92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving && !mediaBusy"
      :show-close="!saving && !mediaBusy"
      :before-close="beforeClose"
    >
      <form
        v-if="editor"
        class="category-dialog-form"
        aria-label="分类编辑"
        @submit.prevent="save"
      >
        <p v-if="error" class="error notice" role="alert">{{ error }}</p>
        <div class="form-grid">
          <label
            >名称<input
              :disabled="saving"
              v-model="name"
              maxlength="80"
              required
              placeholder="请输入分类名称"
          /></label>
          <label
            >排序<input
              :disabled="saving"
              v-model.number="sortOrder"
              type="number"
              min="0"
              step="1"
              required
          /></label>
          <label v-if="editor === 'new'"
            >上级分类<select v-model="parentId" :disabled="saving">
              <option value="">无（一级分类）</option>
              <option
                v-for="root in roots.filter((item) => item.status === 'ACTIVE')"
                :key="root.id"
                :value="root.id"
              >
                {{ root.name }}
              </option>
            </select></label
          >
          <label v-else
            >状态<select v-model="status" :disabled="saving">
              <option value="ACTIVE">启用</option>
              <option value="INACTIVE">停用</option>
            </select></label
          >
        </div>
        <CategoryImageEditor
          v-model:asset="image"
          :secondary="secondary"
          :target-key="
            JSON.stringify([editor === 'new' ? 'new' : editor.id, parentId])
          "
          :can-upload="canUpload"
          :disabled="saving || !canWrite"
          @busy-change="mediaBusy = $event"
        />
        <p v-if="editor !== 'new' && status === 'INACTIVE'" class="hint">
          关联 {{ editor.productCount ?? 0 }} 个商品，其中
          {{ editor.onSaleProductCount ?? 0 }}
          个在售。停用前须先下架在售商品；历史订单的分类快照不会改变。
        </p>
        <button
          class="primary-button"
          type="submit"
          :disabled="saving || mediaBusy || needsRead || !canWrite"
        >
          {{ saving ? "保存中…" : "保存分类" }}
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="saving || mediaBusy"
          @click="closeEditor(false)"
        >
          取消
        </button>
      </form>
    </ElDialog>
  </div>
</template>

<style scoped>
.category-dialog-form .form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.category-dialog-form > .secondary-button {
  margin-left: 12px;
}
@media (max-width: 480px) {
  .category-dialog-form .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
