<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ task: { taskId: string; version: string; status: string; channel?: string;
  failureCode?: string; failureStage?: string; sdkCode?: string; platformErrorCode?: number | null;
  failureMessage?: string; nextAction?: string; completedAt?: string | null } }>()
const label = computed(() => ({ PENDING: '等待上传', RUNNING: '正在编译并上传',
  SUCCEEDED: props.task.channel === 'DIRECT_COMMIT' ? '微信已接收待审核版本' : '微信已接收开发版本', FAILED: '上传失败', UNKNOWN: '结果待核查',
  RESOLVED: '已人工关闭' } as Record<string, string>)[props.task.status] || '状态待核查')
const stage = computed(() => ({ ENVIRONMENT: '运行环境', VALIDATION: '代码校验',
  COMPILE: '代码编译', UPLOAD: '上传微信', RESPONSE: '微信回执' } as Record<string, string>)[props.task.failureStage || ''])
const message = computed(() => props.task.failureMessage || (props.task.status === 'UNKNOWN'
  ? '未取得可确认上传结果的回执。请先在微信公众平台核对该版本，避免重复上传。' : ''))
</script>
<template>
  <div class="upload-task-result" :data-status="task.status">
    <strong>{{ label }}<template v-if="stage"> · {{ stage }}</template></strong>
    <p v-if="message">{{ message }}</p>
    <p v-if="task.nextAction">{{ task.nextAction }}</p>
    <p v-if="task.status === 'SUCCEEDED' && task.channel !== 'DIRECT_COMMIT'">版本 {{ task.version }} 已上传。可前往微信公众平台设置体验版、提交审核和发布。</p>
    <details class="upload-task-details">
      <summary>任务详情</summary>
      <dl><dt>任务号</dt><dd>{{ task.taskId }}</dd>
        <template v-if="task.completedAt"><dt>完成时间</dt><dd>{{ new Date(task.completedAt).toLocaleString('zh-CN', { hour12: false }) }}</dd></template>
        <template v-if="task.failureCode"><dt>结果代码</dt><dd>{{ task.failureCode }}</dd></template>
        <template v-if="task.sdkCode"><dt>SDK 代码</dt><dd>{{ task.sdkCode }}</dd></template>
        <template v-if="task.platformErrorCode != null"><dt>微信错误码</dt><dd>{{ task.platformErrorCode }}</dd></template>
      </dl>
    </details>
  </div>
</template>
