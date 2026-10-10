<script setup lang="ts">
import { ref } from 'vue'
import type { PageComponent } from './types'
const props = defineProps<{ component: PageComponent; disabled?: boolean }>()
const emit = defineEmits<{ change: [value: PageComponent] }>()
const error = ref('')
function update(change: Partial<PageComponent['props']>) { if (!props.disabled) emit('change', { ...props.component, props: { ...props.component.props, ...change } }) }
function source(event: Event) {
  const selected = (event.target as HTMLSelectElement).value as 'MANUAL' | 'AUTO'
  update(selected === 'AUTO' ? { source: selected, campaignIds: [] } : { source: selected })
}
function ids(event: Event) {
  if (props.disabled) return
  const input = event.target as HTMLTextAreaElement
  const campaignIds = input.value.split(/[\s,，]+/).filter(Boolean)
  if (campaignIds.length > 10 || new Set(campaignIds).size !== campaignIds.length || campaignIds.some(id => !/^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/.test(id))) {
    error.value = '活动 ID 未应用：请填写最多 10 个不重复的 UUID，每行一个。'
    input.value = (props.component.props.campaignIds || []).join('\n'); return
  }
  error.value = ''; update({ campaignIds })
}
</script>
<template>
  <fieldset :disabled="disabled" class="coupon-fields">
    <label>优惠券来源<select aria-label="优惠券来源" :value="component.props.source || 'AUTO'" @change="source"><option value="AUTO">自动展示可领取活动</option><option value="MANUAL">指定优惠券活动</option></select></label>
    <label v-if="component.props.source === 'MANUAL'">优惠券活动 ID<textarea :value="(component.props.campaignIds || []).join('\n')" rows="4" placeholder="每行填写一个活动 UUID" @change="ids" /></label>
    <label>优惠券展示数量<input type="number" min="1" max="10" :value="component.props.limit || 3" @change="update({ limit: Number(($event.target as HTMLInputElement).value) })"></label>
    <label>优惠券布局<select aria-label="优惠券布局" :value="component.props.layout || 'LIST'" @change="update({ layout: ($event.target as HTMLSelectElement).value as 'LIST' | 'SCROLL' })"><option value="LIST">纵向列表</option><option value="SCROLL">横向滑动</option></select></label>
    <p class="help-text">切换为自动展示会清除已指定的活动。券面额、使用条件和领取资格由优惠券系统提供。这里配置展示入口，不发放或领取优惠券；预览按游客状态展示。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </fieldset>
</template>
<style scoped>.coupon-fields{border:0;margin:0;padding:0;min-width:0}</style>
