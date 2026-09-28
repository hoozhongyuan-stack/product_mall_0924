<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PageConfig } from './types'

const props = defineProps<{ config: PageConfig; stale: boolean; pageName?: string }>()
const failedAssets = ref<string[]>([])
watch(() => props.config, () => { failedAssets.value = [] })
const visible = computed(() => props.config.components.filter((item) => item.visible).sort((a, b) => a.sortOrder - b.sortOrder))
const theme = computed(() => ({
  '--preview-page': props.config.theme.pageBackgroundColor,
  '--preview-header': props.config.theme.headerBackgroundColor,
  '--preview-brand': props.config.theme.brandTextColor,
}))
function assetUrl(id: string) { return `/api/v1/admin/assets/${encodeURIComponent(id)}/file` }
</script>

<template>
  <div class="home-phone-wrap">
    <div class="home-phone" :style="theme" :aria-label="`服务端校验后的${config.pageType === 'MICRO' ? '独立微页面' : '小程序首页'}预览`">
      <div class="home-phone-status">9:41 <span>●●●</span></div>
      <div class="home-phone-header"><strong>{{ pageName || (config.pageType === 'MICRO' ? '独立微页面' : '商城首页') }}</strong><small>{{ config.pageType === 'MICRO' ? '返回' : '商品分类 ›' }}</small></div>
      <div class="home-phone-body">
        <template v-for="item in visible" :key="item.componentId">
          <div v-if="item.type === 'SEARCH'" class="home-phone-search"><span>{{ item.props.placeholder || '搜索商品' }}</span><b>搜索</b></div>
          <div v-else-if="item.type === 'NOTICE'" class="home-phone-notice"><strong>公告</strong><span>{{ item.props.text }}</span><span v-if="item.props.link">›</span></div>
          <div v-else-if="item.type === 'CAROUSEL'" class="home-phone-media">
            <span v-if="item.props.slides?.[0]?.assetId && failedAssets.includes(item.props.slides[0].assetId)" role="status">轮播图片无法读取，请检查素材</span>
            <img v-else-if="item.props.slides?.[0]?.assetId" :src="assetUrl(item.props.slides[0].assetId)" alt="轮播首图" @error="failedAssets = [...failedAssets, item.props.slides[0].assetId]">
            <span v-else>轮播图尚未配置图片</span>
            <small v-if="(item.props.slides?.length || 0) > 1">1 / {{ item.props.slides?.length }}</small>
          </div>
          <div v-else-if="item.type === 'IMAGE_HOTZONE'" class="home-phone-media">
            <span v-if="item.props.assetId && failedAssets.includes(item.props.assetId)" role="status">热区图片无法读取，请检查素材</span>
            <img v-else-if="item.props.assetId" :src="assetUrl(item.props.assetId)" alt="图片热区素材" @error="failedAssets = [...failedAssets, item.props.assetId]">
            <span v-else>图片热区尚未配置素材</span>
          </div>
          <hr v-else-if="item.type === 'DIVIDER'" class="home-phone-divider" :class="`divider-${item.props.style || 'SOLID'}`">
          <div v-else-if="item.type === 'FILING'" class="home-phone-filing">{{ item.props.recordNo }}</div>
        </template>
        <p v-if="!visible.length" class="home-phone-empty">当前没有显示中的组件</p>
      </div>
      <div v-if="stale" class="home-preview-stale" role="status">编辑内容已变化<br><small>再次预览可查看服务端校验结果</small></div>
    </div>
  </div>
</template>
