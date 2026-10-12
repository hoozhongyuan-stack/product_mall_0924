<script setup lang="ts">
import { ElButton } from 'element-plus'
import type { NavigationFeedback } from './useNavigationFeedback'
defineProps<{ state: NavigationFeedback | null }>()
defineEmits<{ retry: []; dismiss: []; refresh: [] }>()
</script>
<template>
  <section v-if="state" data-navigation-feedback class="navigation-feedback" :class="{ 'navigation-feedback-error': state.phase === 'error' || state.phase === 'blocked' }" role="status" aria-live="polite" aria-atomic="true">
    <div class="navigation-feedback-copy">
      <strong>{{ state.phase === 'error' ? `暂时无法打开${state.title}` : state.phase === 'blocked' ? `无权打开${state.title}` : `正在打开${state.title}…` }}</strong>
      <p v-if="state.phase === 'slow'">页面切换耗时较长，当前内容仍保留。可继续等待或重新尝试。</p>
      <p v-else-if="state.phase === 'error'">页面加载失败，浏览器可能保留失败的页面代码。可先重新尝试；仍失败时，安全刷新会先核对当前页面是否可以离开，再打开目标页面。</p>
      <p v-else-if="state.phase === 'blocked'">当前账号没有此页面的访问权限，请选择其他菜单。</p>
      <p v-else-if="state.phase === 'recovering'">请完成当前页面的离开确认。确认通过后将刷新并打开目标页面。</p>
      <progress v-else class="navigation-feedback-progress" :aria-label="`正在打开${state.title}`" />
    </div>
    <ElButton v-if="state.phase === 'slow' || state.phase === 'error'" @click="$emit('retry')">重新尝试</ElButton>
    <ElButton v-if="state.phase === 'error'" @click="$emit('refresh')">安全刷新并重试</ElButton>
    <ElButton v-if="state.phase === 'error' || state.phase === 'blocked'" text @click="$emit('dismiss')">关闭提示</ElButton>
  </section>
</template>
<style scoped>
.navigation-feedback { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 16px 32px 0; padding: 12px 16px; border: 1px solid var(--mall-color-border); border-radius: var(--mall-radius-control); background: var(--mall-color-surface); color: var(--mall-color-text); }
.navigation-feedback-copy { flex: 1 1 220px; min-width: 0; overflow-wrap: anywhere; }
.navigation-feedback strong { font-size: 14px; font-weight: 600; }
.navigation-feedback p { margin: 6px 0 0; font-size: 14px; line-height: 1.6; color: var(--mall-color-muted); }
.navigation-feedback-error strong { color: var(--mall-color-danger); }
.navigation-feedback-progress { display: block; width: min(100%, 240px); height: 4px; margin-top: 8px; accent-color: var(--mall-color-brand); }
@media (max-width: 700px) { .navigation-feedback { margin: 12px 16px 0; } .navigation-feedback strong { font-size: 16px; } }
@media (prefers-reduced-motion: reduce) { .navigation-feedback-progress { animation: none; } }
</style>
