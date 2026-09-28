<script setup lang="ts">
import { computed, inject, ref, type Ref } from 'vue'
import type { Account } from '../api'
import AssetBrowser from './AssetBrowser.vue'
import { selectionStillCurrent, type AssetKind, type LibraryAsset } from './media-library'
const props = defineProps<{ kind: AssetKind; square?: boolean; excludedIds?: string[]; disabled?: boolean; targetKey: string }>()
const emit = defineEmits<{ select: [asset: LibraryAsset] }>()
const account = inject<Ref<Account | null>>('admin-account')
const canRead = computed(() => !!account?.value?.permissionCodes.includes('asset.read'))
const canUpload = computed(() => !!account?.value?.permissionCodes.includes('asset.upload'))
const open = ref(false)
const expected = ref('')
const error = ref('')
function show() { expected.value = props.targetKey; error.value = ''; open.value = true }
function select(asset: LibraryAsset) {
  if (props.disabled || (!canRead.value && !canUpload.value) || !selectionStillCurrent(expected.value, props.targetKey)) { error.value = '素材位已变化，此次选择未绑定。请取消后重新选择。'; return }
  emit('select', asset)
  open.value = false
}
</script>
<template>
  <el-button v-if="canRead || canUpload" :disabled="disabled" @click="show">从素材中心选择</el-button>
  <el-dialog class="asset-dialog" v-model="open" title="选择素材" width="960px" :close-on-click-modal="false" destroy-on-close>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <AssetBrowser v-if="open" :can-read="canRead" :can-upload="canUpload" :kind="kind" :square="square" :excluded-ids="excludedIds" selecting @select="select" />
    <template #footer><el-button @click="open = false">取消选择</el-button></template>
  </el-dialog>
</template>
