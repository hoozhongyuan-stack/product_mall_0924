<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Account } from '../api'
import { onBeforeRouteLeave } from 'vue-router'
import { confirmLeavingUploads } from '../shared/upload-navigation'
import AssetBrowser from '../shared/AssetBrowser.vue'
const props = defineProps<{ account: Account }>()
const canRead = computed(() => props.account.permissionCodes.includes('asset.read'))
const canUpload = computed(() => props.account.permissionCodes.includes('asset.upload'))
const pendingUploads = ref(false)
onBeforeRouteLeave(() => pendingUploads.value ? confirmLeavingUploads() : true)
</script>
<template>
  <section class="page-content assets-page"><header class="page-heading"><div><h1>素材中心</h1><p>统一管理商品、页面与启动配置使用的图片、视频和 GIF。</p></div></header><section class="panel"><AssetBrowser :can-read="canRead" :can-upload="canUpload" @pending="pendingUploads = $event" /></section></section>
</template>
