<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useCouponPreview } from './coupon-preview'
import { useProductPreview } from './product-preview'
import './preview-content.css'
import type { ComponentData, CouponData, PageComponent, PageConfig } from './types'

const props = defineProps<{ config: PageConfig; stale: boolean; pageName?: string; interactive?: boolean; selectedId?: string; componentData?: ComponentData; couponData?: CouponData }>()
const emit = defineEmits<{ select: [id: string] }>()
const carouselIndex = ref<Record<string, number>>({})
const { data: liveData, error: productError, loading: productsLoading } = useProductPreview(computed(() => props.config.components), computed(() => !!props.interactive))
const { data: liveCoupons, error: couponError, loading: couponsLoading } = useCouponPreview(computed(() => props.config.components), computed(() => !!props.interactive))
function coupons(id: string) { return props.interactive ? liveCoupons.value[id] || [] : props.couponData?.[id] || [] }
function couponState(state: string) { return ({ GUEST: '登录后领取', AVAILABLE: '可领取', LIMIT_REACHED: '已达领取上限', SOLD_OUT: '已领完', EXPIRED: '活动已结束', NOT_STARTED: '活动未开始', UNAVAILABLE: '暂不可领取' } as Record<string, string>)[state] || '暂不可领取' }
function products(id: string) { return props.interactive ? liveData.value[id] || [] : props.componentData?.[id] || [] }
function select(item: PageComponent) { if (props.interactive) emit('select', item.componentId) }
function keySelect(event: KeyboardEvent, item: PageComponent) { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); select(item) } }
function appearance(item: PageComponent) { const value = item.appearance; return value ? { backgroundColor: value.backgroundColor, padding: `${value.padding || 0}px`, margin: `${value.margin || 0}px`, borderRadius: `${value.radius || 0}px` } : {} }
function slideIndex(item: PageComponent) { return Math.min(carouselIndex.value[item.componentId] || 0, Math.max(0, (item.props.slides?.length || 1) - 1)) }
function nextSlide(item: PageComponent) { carouselIndex.value = { ...carouselIndex.value, [item.componentId]: (slideIndex(item) + 1) % (item.props.slides?.length || 1) } }
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
    <div class="home-phone" :style="theme" :aria-label="`${interactive ? '即时效果' : '服务端校验后的'}${config.pageType === 'MICRO' ? '独立微页面' : '小程序首页'}预览`">
      <div class="home-phone-status">9:41 <span>●●●</span></div>
      <div class="home-phone-header"><strong>{{ pageName || (config.pageType === 'MICRO' ? '独立微页面' : '商城首页') }}</strong><small>{{ config.pageType === 'MICRO' ? '返回' : '商品分类 ›' }}</small></div>
      <div class="home-phone-body">
        <section v-for="item in visible" :key="item.componentId" class="preview-component" :class="{ 'preview-selected': interactive && selectedId === item.componentId, 'preview-interactive': interactive }" :style="appearance(item)" :role="interactive ? 'button' : undefined" :tabindex="interactive ? 0 : undefined" :aria-label="interactive ? `选择${item.type}组件` : undefined" @click="select(item)" @keydown="keySelect($event, item)">
          <div v-if="item.type === 'SEARCH'" class="home-phone-search"><span>{{ item.props.placeholder || '搜索商品' }}</span><b>搜索</b></div>
          <div v-else-if="item.type === 'NOTICE'" class="home-phone-notice"><strong>公告</strong><span>{{ item.props.text }}</span><span v-if="item.props.link">›</span></div>
          <div v-else-if="item.type === 'CAROUSEL'" class="home-phone-media">
            <span v-if="item.props.slides?.[slideIndex(item)]?.assetId && failedAssets.includes(item.props.slides[slideIndex(item)].assetId)" role="status">轮播图片无法读取，请检查素材</span>
            <img v-else-if="item.props.slides?.[slideIndex(item)]?.assetId" :src="assetUrl(item.props.slides[slideIndex(item)].assetId)" alt="轮播首图" @error="failedAssets = [...failedAssets, item.props.slides[slideIndex(item)].assetId]">
            <span v-else>轮播图尚未配置图片</span>
            <button v-if="(item.props.slides?.length || 0) > 1" type="button" class="preview-carousel-next" @click.stop="nextSlide(item)">{{ slideIndex(item) + 1 }} / {{ item.props.slides?.length }} · 下一张</button>
          </div>
          <div v-else-if="item.type === 'IMAGE_HOTZONE'" class="home-phone-media hotzone-preview">
            <span v-if="item.props.assetId && failedAssets.includes(item.props.assetId)" role="status">热区图片无法读取，请检查素材</span>
            <img v-else-if="item.props.assetId" :src="assetUrl(item.props.assetId)" alt="图片热区素材" @error="failedAssets = [...failedAssets, item.props.assetId]">
            <span v-else>图片热区尚未配置素材</span>
            <span v-for="(area, index) in item.props.areas || []" :key="index" class="preview-hotzone" :style="{ left: `${area.x * 100}%`, top: `${area.y * 100}%`, width: `${area.width * 100}%`, height: `${area.height * 100}%` }">{{ index + 1 }}</span>
          </div>
          <hr v-else-if="item.type === 'DIVIDER'" class="home-phone-divider" :class="`divider-${item.props.style || 'SOLID'}`">
          <div v-else-if="item.type === 'FILING'" class="home-phone-filing">{{ item.props.recordNo }}</div>
          <div v-else-if="item.type === 'COUPON_LIST'" class="preview-coupons" :class="`preview-coupons-${item.props.layout || 'LIST'}`"><article v-for="coupon in coupons(item.componentId)" :key="coupon.id"><div class="preview-coupon-amount">¥{{ (coupon.discountFen / 100).toFixed(2) }}<small>{{ coupon.minGoodsFen ? `满${(coupon.minGoodsFen / 100).toFixed(2)}可用` : '无金额门槛' }}</small></div><div class="preview-coupon-copy"><strong>{{ coupon.title }}</strong><small>{{ couponState(coupon.claimState) }}</small><small>{{ coupon.validUntil.slice(0, 10) }} 前有效</small></div></article><p v-if="!coupons(item.componentId).length" class="preview-product-empty">{{ couponsLoading ? '正在读取优惠券…' : couponError || '暂无可展示优惠券，请配置活动来源' }}</p></div>
          <div v-else-if="item.type === 'MOSAIC'" class="preview-mosaic" :class="`preview-mosaic-${item.props.template || 'TWO'}`" :style="{ gap: `${item.props.gap || 0}px` }"><div v-for="(entry, index) in item.props.items || []" :key="index"><img v-if="entry.assetId && !failedAssets.includes(entry.assetId)" :src="assetUrl(entry.assetId)" alt="" @error="failedAssets = [...failedAssets, entry.assetId]"><span v-else class="preview-mosaic-empty">{{ entry.assetId ? '图片不可用' : `配置图片 ${index + 1}` }}</span><span v-if="entry.title" class="preview-mosaic-title">{{ entry.title }}</span></div></div>
          <div v-else-if="item.type === 'SPACER'" class="preview-spacer" :style="{ height: `${item.props.height || 16}px` }" :aria-label="`留白 ${item.props.height || 16}px`" />
          <div v-else-if="item.type === 'TITLE'" class="preview-title" :style="{ textAlign: item.props.align === 'CENTER' ? 'center' : 'left' }"><strong :style="{ fontSize: `${item.props.size || 20}px` }">{{ item.props.text || '填写标题' }}</strong><p v-if="item.props.subtitle">{{ item.props.subtitle }}</p></div>
          <div v-else-if="item.type === 'IMAGE'" class="preview-image"><img v-if="item.props.assetId && !failedAssets.includes(item.props.assetId)" :src="assetUrl(item.props.assetId)" alt="图片广告" :style="{ aspectRatio: item.props.ratio === '1:1' ? '1' : item.props.ratio === '16:9' ? '16 / 9' : undefined }" @error="failedAssets = [...failedAssets, item.props.assetId]"><span v-else>{{ item.props.assetId ? '图片无法读取，请检查素材' : '图片广告尚未配置素材' }}</span></div>
          <div v-else-if="item.type === 'NAVIGATION'" class="preview-navigation" :style="{ gridTemplateColumns: `repeat(${item.props.columns || 4}, minmax(0, 1fr))` }"><div v-for="(entry, index) in item.props.items || []" :key="index"><img v-if="entry.assetId && !failedAssets.includes(entry.assetId)" :src="assetUrl(entry.assetId)" alt="" @error="failedAssets = [...failedAssets, entry.assetId]"><span v-else-if="entry.assetId" class="preview-image-error">图片不可用</span><span>{{ entry.title || '导航名称' }}</span></div><p v-if="!item.props.items?.length">添加图文导航入口</p></div>
          <div v-else-if="item.type === 'PRODUCT_LIST'" class="preview-products" :class="`preview-products-${item.props.layout || 'GRID'}`"><article v-for="product in products(item.componentId)" :key="product.productId"><img v-if="product.imageUrl && !failedAssets.includes(product.imageUrl)" :src="product.imageUrl" alt="" @error="failedAssets = [...failedAssets, product.imageUrl]"><span v-else class="preview-product-image-placeholder">商品图片</span><div><strong>{{ product.name }}</strong><p class="preview-price">¥{{ (product.priceFen / 100).toFixed(2) }} 起</p><small v-if="!product.purchasable">暂不可购买</small></div></article><p v-if="!products(item.componentId).length" class="preview-product-empty">{{ productsLoading ? '正在读取商品…' : productError || '暂无可展示商品，请配置商品来源' }}</p></div>
        </section>
        <p v-if="!visible.length" class="home-phone-empty">当前没有显示中的组件</p>
      </div>
      <div v-if="stale" class="home-preview-stale" role="status">编辑内容已变化<br><small>再次预览可查看服务端校验结果</small></div>
    </div>
  </div>
</template>
