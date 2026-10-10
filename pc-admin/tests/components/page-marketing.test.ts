import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { useCouponPreview } from '../../src/views/pages/coupon-preview'
import { validateReleaseReport, type ReleaseReport } from '../../src/views/pages/release-report'
import HomePagePreview from '../../src/views/pages/HomePagePreview.vue'
import PageComponentEditor from '../../src/views/pages/PageComponentEditor.vue'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
import { api } from '../../src/api'
import CouponFields from '../../src/views/pages/CouponFields.vue'
import ReleaseReportPanel from '../../src/views/pages/ReleaseReportPanel.vue'
import { createComponent, normalizeConfig, type PageConfig } from '../../src/views/pages/types'
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
const config = (): PageConfig => ({ schemaVersion: 3, pageType: 'MICRO', theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#111111' }, components: [] })
describe('coupon configuration schema 4', () => {
  it('promotes coupon components without downgrading older configuration', () => {
    const coupon = createComponent('COUPON_LIST', 1)
    expect(coupon.props).toEqual({ source: 'AUTO', campaignIds: [], limit: 3, layout: 'LIST' })
    expect(normalizeConfig({ ...config(), components: [coupon] }).schemaVersion).toBe(4)
    expect(normalizeConfig({ ...config(), schemaVersion: 4 }).schemaVersion).toBe(4)
    expect(() => normalizeConfig({ ...config(), schemaVersion: 5 } as never)).toThrow('更新')
  })
  it('keeps exact accessible names and clears manual selections when switching to automatic', async () => {
    const component = createComponent('COUPON_LIST', 1)
    const manual = { ...component, props: { ...component.props, source: 'MANUAL' as const, campaignIds: ['11111111-1111-1111-1111-111111111111'] } }
    const wrapper = mount(CouponFields, { props: { component: manual } })
    expect(wrapper.get('select').attributes('aria-label')).toBe('优惠券来源')
    expect(wrapper.findAll('select')[1]!.attributes('aria-label')).toBe('优惠券布局')
    await wrapper.get('select').setValue('AUTO')
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { source: 'AUTO', campaignIds: [] } })
    expect(manual.props.campaignIds).toHaveLength(1)
    const automatic = wrapper.emitted('change')!.at(-1)![0] as typeof component
    await wrapper.setProps({ component: automatic })
    await wrapper.get('select').setValue('MANUAL')
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { source: 'MANUAL', campaignIds: [] } })
    wrapper.unmount()
  })
  it('changes campaign sources through controlled form events and retains immutable input', async () => {
    const component = createComponent('COUPON_LIST', 1)
    const wrapper = mount(CouponFields, { props: { component } })
    await wrapper.get('select').setValue('MANUAL')
    const next = wrapper.emitted('change')![0]![0] as typeof component
    await wrapper.setProps({ component: next })
    await wrapper.get('textarea').setValue('11111111-1111-1111-1111-111111111111\n22222222-2222-2222-2222-222222222222')
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { campaignIds: ['11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'] } })
    expect(component.props.campaignIds).toEqual([])
    await wrapper.get('input').setValue('8')
    await wrapper.findAll('select')[1]!.setValue('SCROLL')
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { layout: 'SCROLL' } })
    wrapper.unmount()
  })
  it('does not claim stale release reports are ready to publish', () => {
    const wrapper = mount(ReleaseReportPanel, { props: { report: null, stale: true, loading: false, error: '', disabled: false } })
    expect(wrapper.text()).toContain('尚未检查')
    wrapper.unmount()
  })
})
const report = (): ReleaseReport => ({ pageId: 'p1', revision: 3, publicationRevision: 2, publishedVersionId: 'v1', schemaVersion: 4, runtimeSchemaVersion: 4, runtimeSupported: true, canPublish: true, diff: { addedComponentIds: [], removedComponentIds: [], updatedComponentIds: [], orderChanged: false, themeChanged: false, metadataChanged: false }, issues: [] })
const coupon = () => ({ id: '11111111-1111-1111-1111-111111111111', title: '活动券', kind: 'CASH', minGoodsFen: 10000, discountFen: 1000, productIds: [], productNames: [], redeemEligible: true, validFrom: '2026-10-01', validUntil: '2026-12-31', remainingQuantity: 5, selfClaimLimit: 1, selfClaimedCount: 0, canClaim: false, claimState: 'GUEST' })
describe('coupon input and guest preview safeguards', () => {
  it('rejects invalid campaign ids without changing applied values, and prevents disabled writes', async () => {
    const component = createComponent('COUPON_LIST', 1); component.props.source = 'MANUAL'; component.props.campaignIds = [coupon().id]
    const wrapper = mount(CouponFields, { props: { component } })
    await wrapper.get('textarea').setValue('not-uuid')
    expect(wrapper.text()).toContain('ID 未应用'); expect(wrapper.emitted('change')).toBeUndefined()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(coupon().id)
    await wrapper.get('textarea').setValue(`${coupon().id}\n${coupon().id}`); expect(wrapper.emitted('change')).toBeUndefined()
    await wrapper.get('textarea').setValue(''); expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { campaignIds: [] } })
    const count = wrapper.emitted('change')!.length
    await wrapper.setProps({ disabled: true }); await wrapper.get('textarea').setValue(coupon().id); await wrapper.get('select').setValue('AUTO')
    expect(wrapper.emitted('change')).toHaveLength(count)
    wrapper.unmount()
  })
  it('does not let an old request restore outdated coupon activities', async () => {
    vi.useFakeTimers()
    const component = createComponent('COUPON_LIST', 1), components = ref([component])
    let oldResolve!: (value: unknown) => void
    vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { oldResolve = resolve })).mockResolvedValueOnce({ coupons: [{ ...coupon(), title: '新活动券' }] })
    let preview!: ReturnType<typeof useCouponPreview>
    const wrapper = mount(defineComponent({ setup() { preview = useCouponPreview(components, ref(true)); return () => null } }))
    await vi.advanceTimersByTimeAsync(250)
    components.value = [{ ...component, props: { ...component.props, source: 'MANUAL', campaignIds: [coupon().id] } }]; await flushPromises(); await vi.advanceTimersByTimeAsync(250)
    oldResolve({ coupons: [{ ...coupon(), title: '过期响应' }] }); await flushPromises()
    expect(preview.data.value[component.componentId]?.[0]?.title).toBe('新活动券')
    wrapper.unmount()
  })
  it('reports missing permission and malformed coupon amounts instead of inventing a coupon', async () => {
    vi.useFakeTimers()
    const component = createComponent('COUPON_LIST', 1), components = ref([component]), enabled = ref(true)
    vi.mocked(api).mockRejectedValueOnce(new Error('403')).mockResolvedValueOnce({ coupons: [{ ...coupon(), discountFen: -1 }] })
    let preview!: ReturnType<typeof useCouponPreview>
    const wrapper = mount(defineComponent({ setup() { preview = useCouponPreview(components, enabled); return () => null } }))
    await vi.advanceTimersByTimeAsync(250); expect(preview.error.value).toContain('权限'); expect(preview.data.value).toEqual({})
    components.value = [{ ...component, props: { ...component.props, limit: 4 } }]; await flushPromises(); await vi.advanceTimersByTimeAsync(250); expect(preview.data.value).toEqual({})
    enabled.value = false; await flushPromises(); expect(preview.loading.value).toBe(false)
    wrapper.unmount()
  })
  it('renders guest, availability and ending states without any real claim action', async () => {
    const component = createComponent('COUPON_LIST', 1)
    const items = ['GUEST', 'AVAILABLE', 'LIMIT_REACHED', 'SOLD_OUT', 'EXPIRED', 'NOT_STARTED', 'UNAVAILABLE', 'unknown'].map((claimState, index) => ({ ...coupon(), id: String(index), claimState, minGoodsFen: index ? 10000 : 0 }))
    const wrapper = mount(HomePagePreview, { props: { config: { ...config(), components: [component] }, stale: false, couponData: { [component.componentId]: items } } })
    expect(wrapper.text()).toContain('无金额门槛'); expect(wrapper.text()).toContain('10.00'); expect(wrapper.text()).toContain('已领完'); expect(wrapper.text()).toContain('登录后领取')
    expect(wrapper.find('a[href]').exists()).toBe(false)
    await wrapper.setProps({ interactive: true }); expect(wrapper.text()).not.toContain('活动券')
    await wrapper.setProps({ interactive: false, config: { ...config(), components: [{ ...component, props: {} }] }, couponData: {} }); expect(wrapper.text()).toContain('暂无可展示优惠券')
    wrapper.unmount()
    const editor = mount(PageComponentEditor, { props: { component, canUpload: false, categories: [], products: [], pages: [] }, global: { stubs: { AssetPicker: true } } })
    editor.getComponent(CouponFields).vm.$emit('change', { ...component, props: { ...component.props, limit: 5 } }); expect(editor.emitted('change')![0]![0]).toMatchObject({ props: { limit: 5 } }); editor.unmount()
  })
})
describe('release report accuracy and controlled presentation', () => {
  it('reports a name-only publication difference and validates the optional name flag', () => {
    const renamed = { ...report(), diff: { ...report().diff, nameChanged: true } }
    expect(validateReleaseReport(renamed)).toBe(renamed)
    expect(() => validateReleaseReport({ ...renamed, diff: { ...renamed.diff, nameChanged: 'yes' } })).toThrow('格式')
    expect(validateReleaseReport({ ...renamed, diff: { ...renamed.diff, nameChanged: false } }).diff).toMatchObject({ nameChanged: false })
    const wrapper = mount(ReleaseReportPanel, { props: { report: renamed, stale: false, loading: false, error: '' } })
    expect(wrapper.text()).toContain('页面名称变化')
    expect(wrapper.text()).not.toContain('与当前线上内容一致')
    wrapper.unmount()
  })
  it('validates all required shape fields and rejects malformed reports', () => {
    const good = report(); expect(validateReleaseReport(good)).toBe(good)
    expect(() => validateReleaseReport(null)).toThrow('格式')
    for (const bad of [{ ...good, revision: -1 }, { ...good, diff: { ...good.diff, addedComponentIds: [1] } }, { ...good, issues: [{ code: 'X', path: 'x', message: '错误' }] }, { ...good, publishedVersionId: 42 }]) expect(() => validateReleaseReport(bad)).toThrow('格式')
    expect(validateReleaseReport({ ...good, publishedVersionId: null, issues: [{ componentId: 'coupon', path: 'props.ids', code: 'BAD', message: '活动失效', severity: 'ERROR' }] }).issues).toHaveLength(1)
  })
  it('shows accurate differences, invalid references and runtime incompatibility then hides stale conclusions', async () => {
    const current = { ...report(), publishedVersionId: null, runtimeSupported: false, runtimeSchemaVersion: 3, canPublish: false, diff: { addedComponentIds: ['a'], removedComponentIds: ['b'], updatedComponentIds: ['c'], orderChanged: true, themeChanged: true, metadataChanged: true }, issues: [{ componentId: 'c', path: 'props.campaignIds', code: 'COUPON_INVALID', message: '指定优惠券活动不可用', severity: 'ERROR' as const }, { path: 'schemaVersion', code: 'UNSUPPORTED', message: '小程序版本需要更新', severity: 'WARNING' as const }] }
    const wrapper = mount(ReleaseReportPanel, { props: { report: current, stale: false, loading: false, error: '', disabled: false } })
    expect(wrapper.text()).toContain('阻断项'); expect(wrapper.text()).toContain('顺序变化'); expect(wrapper.text()).toContain('资料变化'); expect(wrapper.text()).toContain('优惠券活动不可用'); expect(wrapper.text()).toContain('不兼容')
    await wrapper.get('button').trigger('click'); expect(wrapper.emitted('refresh')).toHaveLength(1)
    await wrapper.setProps({ stale: true }); expect(wrapper.text()).toContain('报告已过期'); expect(wrapper.text()).not.toContain('当前存在发布阻断项')
    await wrapper.setProps({ loading: true, error: '读取失败' }); expect(wrapper.text()).toContain('读取失败'); expect(wrapper.text()).toContain('正在读取')
    await wrapper.setProps({ loading: false, disabled: true }); await wrapper.get('button').trigger('click'); expect(wrapper.emitted('refresh')).toHaveLength(1)
    await wrapper.setProps({ disabled: false, stale: false, report: report() }); expect(wrapper.text()).toContain('与当前线上内容一致'); expect(wrapper.text()).toContain('检查通过')
    wrapper.unmount()
  })
})
