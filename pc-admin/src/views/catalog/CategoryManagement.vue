<script setup lang="ts">
import { confirmAction } from '../../shared/confirm'

import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, api, type Account } from '../../api'
import type { Category, CategoryStatus } from './types'

const props = defineProps<{ account: Account }>()
const canWrite = computed(() => props.account.permissionCodes.includes('catalog.write'))
const categories = ref<Category[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const editor = ref<Category | 'new' | null>(null)
const name = ref('')
const parentId = ref('')
const sortOrder = ref(0)
const status = ref<CategoryStatus>('ACTIVE')
const roots = computed(() => categories.value.filter((item) => !item.parentId).sort(byOrder))
const rows = computed(() => roots.value.flatMap((root) => [root, ...categories.value.filter((item) => item.parentId === root.id).sort(byOrder)]))
function byOrder(a: Category, b: Category) { return a.sortOrder - b.sortOrder || a.name.localeCompare(b.name) }

async function load() {
  loading.value = true
  error.value = ''
  try { categories.value = await api<Category[]>('/categories') }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '分类加载失败。' }
  finally { loading.value = false }
}
onMounted(load)

function openCreate(parent = '') {
  editor.value = 'new'
  name.value = ''
  parentId.value = parent
  sortOrder.value = 0
  status.value = 'ACTIVE'
  error.value = ''
}

function openEdit(item: Category) {
  editor.value = item
  name.value = item.name
  parentId.value = item.parentId || ''
  sortOrder.value = item.sortOrder
  status.value = item.status
  error.value = ''
}

async function save() {
  const target = editor.value
  if (!target || saving.value) return
  if (!name.value.trim() || !Number.isInteger(sortOrder.value) || sortOrder.value < 0) {
    error.value = '请填写分类名称和非负整数排序值。'
    return
  }
  if (target !== 'new' && target.status === 'ACTIVE' && status.value === 'INACTIVE') {
    if (target.onSaleProductCount) {
      error.value = `该分类影响 ${target.onSaleProductCount} 个在售商品，请先下架后再停用。`
      return
    }
    if (target.productCount && !await confirmAction(`停用后，该分类及下级关联的 ${target.productCount} 个商品将不再展示。确定停用吗？`)) return
  }
  if (saving.value || editor.value !== target) return
  saving.value = true
  error.value = ''
  try {
    if (target === 'new') {
      await api<Category>('/categories', { method: 'POST', body: JSON.stringify({ parentId: parentId.value || null, name: name.value.trim(), sortOrder: sortOrder.value }) })
    } else {
      await api<Category>(`/categories/${target.id}`, { method: 'PATCH', body: JSON.stringify({ name: name.value.trim(), sortOrder: sortOrder.value, status: status.value, expectedRevision: target.revision }) })
    }
    editor.value = null
    ElMessage.success('分类已保存')
    await load()
  } catch (reason) {
    if (reason instanceof ApiError && reason.code === 'REVISION_CONFLICT') {
      editor.value = null
      await load()
      error.value = '分类已被其他人修改。列表已刷新，请重新打开后核对。'
    } else { error.value = reason instanceof Error ? reason.message : '分类保存失败。' }
  } finally { saving.value = false }
}
</script>

<template>
  <div class="catalog-section">
    <div class="page-heading"><div><h2>商品分类</h2><p>最多两级，支持调整同级顺序。商品绑定启用的二级分类。</p></div>
      <button v-if="canWrite" class="primary-button" type="button" @click="openCreate()">新建一级分类</button>
    </div>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载分类…</p>
    <div v-else class="panel table-wrap category-table">
      <table><thead><tr><th scope="col">分类名称</th><th scope="col">层级</th><th scope="col">关联商品</th><th scope="col">排序</th><th scope="col">状态</th><th v-if="canWrite" scope="col">操作</th></tr></thead>
        <tbody><tr v-for="item in rows" :key="item.id">
          <td :class="['strong-cell', { 'child-category': !!item.parentId }]">{{ item.name }}</td>
          <td>{{ item.parentId ? '二级' : '一级' }}</td><td>{{ item.productCount ?? 0 }}</td><td>{{ item.sortOrder }}</td>
          <td><span :class="['badge', item.status === 'ACTIVE' ? 'badge-good' : 'badge-muted']">{{ item.status === 'ACTIVE' ? '启用' : '停用' }}</span></td>
          <td v-if="canWrite"><button class="text-button" type="button" @click="openEdit(item)">编辑</button>
            <button v-if="!item.parentId && item.status === 'ACTIVE'" class="text-button" type="button" @click="openCreate(item.id)">添加二级</button></td>
        </tr></tbody></table>
      <p v-if="!rows.length" class="empty-state">尚无分类。先创建一级分类，再添加二级分类，商品才能选择分类。</p>
    </div>
    <button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>
    <form v-if="editor" class="panel action-panel" @submit.prevent="save">
      <div class="panel-heading"><h3>{{ editor === 'new' ? '新增分类' : `编辑 ${editor.name}` }}</h3><button class="text-button" type="button" @click="editor = null">取消</button></div>
      <div class="form-grid">
        <label>名称<input v-model="name" maxlength="80" required placeholder="请输入分类名称" /></label>
        <label>排序<input v-model.number="sortOrder" type="number" min="0" step="1" required /></label>
        <label v-if="editor === 'new'">上级分类<select v-model="parentId"><option value="">无（一级分类）</option><option v-for="root in roots.filter((item) => item.status === 'ACTIVE')" :key="root.id" :value="root.id">{{ root.name }}</option></select></label>
        <label v-else>状态<select v-model="status"><option value="ACTIVE">启用</option><option value="INACTIVE">停用</option></select></label>
      </div>
      <p v-if="editor !== 'new' && status === 'INACTIVE'" class="hint">关联 {{ editor.productCount ?? 0 }} 个商品，其中 {{ editor.onSaleProductCount ?? 0 }} 个在售。停用前须先下架在售商品；历史订单的分类快照不会改变。</p>
      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存分类' }}</button>
    </form>
  </div>
</template>
