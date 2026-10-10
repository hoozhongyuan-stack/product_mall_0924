import { onUnmounted, ref, watch, type Ref } from 'vue'
import { api } from '../../api'
import type { CouponCard, CouponData, PageComponent } from './types'
function validCoupons(value: unknown): value is CouponCard[] {
  return Array.isArray(value) && value.length <= 10 && value.every(item => item && typeof item.id === 'string' && typeof item.title === 'string' && Number.isSafeInteger(item.discountFen) && item.discountFen >= 0 && Number.isSafeInteger(item.minGoodsFen) && item.minGoodsFen >= 0 && typeof item.canClaim === 'boolean' && typeof item.validFrom === 'string' && typeof item.validUntil === 'string')
}
export function useCouponPreview(components: Ref<PageComponent[]>, enabled: Ref<boolean>) {
  const data = ref<CouponData>({}), error = ref(''), loading = ref(false)
  let sequence = 0, timer: ReturnType<typeof setTimeout> | undefined
  async function load(current: number, items: PageComponent[]) {
    const results = await Promise.allSettled(items.map(async item => {
      const result = await api<{ coupons: CouponCard[] }>('/pages/coupon-preview', { method: 'POST', body: JSON.stringify({ props: item.props }) })
      if (!validCoupons(result.coupons)) throw new Error('优惠券预览格式不正确。')
      return [item.componentId, result.coupons] as const
    }))
    if (sequence !== current) return
    data.value = Object.fromEntries(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []))
    error.value = results.some(result => result.status === 'rejected') ? '优惠券暂无法预览，请检查活动来源与优惠券读取权限后重试。' : ''
    loading.value = false
  }
  watch(() => JSON.stringify([enabled.value, components.value.filter(item => item.type === 'COUPON_LIST').map(item => [item.componentId, item.visible, item.props])]), () => {
    const current = ++sequence
    if (timer) clearTimeout(timer)
    data.value = {}; error.value = ''; loading.value = false
    const items = components.value.filter(item => item.visible && item.type === 'COUPON_LIST')
    if (!enabled.value || !items.length) return
    loading.value = true; timer = setTimeout(() => { void load(current, items) }, 250)
  }, { immediate: true })
  onUnmounted(() => { sequence++; if (timer) clearTimeout(timer) })
  return { data, error, loading }
}
