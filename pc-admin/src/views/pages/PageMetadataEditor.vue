<script setup lang="ts">
import { computed, ref } from 'vue'
import AssetPicker from '../../shared/AssetPicker.vue'
import type { PageMetadata } from './types'
const props = defineProps<{ metadata?: PageMetadata; disabled?: boolean }>()
const emit = defineEmits<{ change: [value: PageMetadata] }>()
const value = computed<PageMetadata>(() => props.metadata || { tags: [], share: { title: '', description: '', coverAssetId: '' } })
const error = ref('')
function tags(raw: string, event?: Event) {
  if (props.disabled) return
  const tags = [...new Set(raw.split(/[,，\n]/).map(item => item.trim()).filter(Boolean))]
  if (tags.length > 5 || tags.some(item => item.length > 20)) { error.value = '标签未应用：最多 5 个标签，每个标签不超过 20 字。'; if (event) (event.target as HTMLInputElement).value = value.value.tags.join(', '); return }
  error.value = ''; emit('change', { ...value.value, tags, share: { ...value.value.share } })
}
function share(change: Partial<PageMetadata['share']>) {
  if (props.disabled) return
  error.value = ''; emit('change', { tags: [...value.value.tags], share: { ...value.value.share, ...change } })
}
</script>
<template>
  <details class="page-metadata"><summary>业务标签与分享资料</summary><fieldset :disabled="disabled">
    <label>业务标签<input data-field="tags" :value="value.tags.join(', ')" placeholder="逗号分隔，最多 5 个标签" @change="tags(($event.target as HTMLInputElement).value, $event)"></label>
    <label>分享标题<input :value="value.share.title" maxlength="60" @input="share({ title: ($event.target as HTMLInputElement).value })"></label>
    <label>分享描述<textarea :value="value.share.description" maxlength="120" rows="2" @input="share({ description: ($event.target as HTMLTextAreaElement).value })" /></label>
    <label>分享封面素材 ID<input :value="value.share.coverAssetId" placeholder="从素材中心选择图片" @input="share({ coverAssetId: ($event.target as HTMLInputElement).value.trim() })"></label>
    <AssetPicker kind="IMAGE" :disabled="disabled" :target-key="JSON.stringify(value)" @select="share({ coverAssetId: $event.assetId })" />
    <button v-if="value.share.coverAssetId" type="button" @click="share({ coverAssetId: '' })">清除封面</button>
    <p class="help-text">微信消息仅支持标题和封面；分享描述用于页面分享配置，尚不承诺在微信消息中展示。这里不生成小程序码。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </fieldset></details>
</template>
<style scoped>.page-metadata{margin:12px 0;padding:12px 16px;border:1px solid var(--mall-color-border);border-radius:12px}.page-metadata summary{cursor:pointer;font-weight:600}.page-metadata fieldset{border:0;padding:12px 0 0;margin:0;display:grid;gap:12px;min-width:0}.page-metadata label{display:grid;gap:6px}.page-metadata input,.page-metadata textarea{padding:9px;border:1px solid var(--mall-color-border);border-radius:8px;min-width:0;width:100%;box-sizing:border-box}</style>
