<script setup lang="ts">
import { computed, inject, ref, watch, type Ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import type { Account } from '../api'
import { confirmLeavingUploads } from './upload-navigation'
import AssetBrowser from './AssetBrowser.vue'
import { selectionStillCurrent, type AssetKind, type LibraryAsset } from './media-library'
const props = defineProps<{ kind: AssetKind; square?: boolean; excludedIds?: string[]; disabled?: boolean; targetKey: string }>()
const emit = defineEmits<{ select: [asset: LibraryAsset] }>()
const account = inject<Ref<Account | null>>('admin-account')
const canRead = computed(() => !!account?.value?.permissionCodes.includes('asset.read'))
const canUpload = computed(() => !!account?.value?.permissionCodes.includes('asset.upload'))
const route = useRoute()
const open = ref(false)
const pendingUploads = ref(false)
async function canNavigate() { return !open.value || !pendingUploads.value || await confirmLeavingUploads() }
onBeforeRouteLeave(canNavigate)
onBeforeRouteUpdate(canNavigate)
// Keep queued files if a later guard cancels; stop them only after navigation commits.
watch(() => route?.fullPath, () => { open.value = false }, { flush: 'sync' })
async function close(done?: () => void) {
  if (pendingUploads.value && !await confirmLeavingUploads()) return
  open.value = false
  done?.()
}
const expected = ref('')
const error = ref('')
function show() { expected.value = props.targetKey; error.value = ''; open.value = true }
function select(asset: LibraryAsset) {
  if (pendingUploads.value || props.disabled || (!canRead.value && !canUpload.value) || !selectionStillCurrent(expected.value, props.targetKey)) { error.value = '素材位已变化，此次选择未绑定。请取消后重新选择。'; return }
  emit('select', asset)
  open.value = false
}
</script>
<template>
  <el-button v-if="canRead || canUpload" :disabled="disabled" @click="show">从素材中心选择</el-button>
  <el-dialog class="asset-dialog" v-model="open" title="选择素材" width="960px" :close-on-click-modal="false" destroy-on-close :before-close="close">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <AssetBrowser v-if="open" :can-read="canRead" :can-upload="canUpload" :kind="kind" :square="square" :excluded-ids="excludedIds" selecting @select="select" @pending="pendingUploads = $event" />
    <template #footer><el-button @click="close()">取消选择</el-button></template>
  </el-dialog>
</template>
