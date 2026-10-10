export type ComponentType = 'SEARCH' | 'NOTICE' | 'CAROUSEL' | 'IMAGE_HOTZONE' | 'DIVIDER' | 'FILING' | 'TITLE' | 'IMAGE' | 'NAVIGATION' | 'PRODUCT_LIST' | 'MOSAIC' | 'SPACER' | 'COUPON_LIST'
export type LinkType = 'PRODUCT' | 'CATEGORY' | 'FUNCTION' | 'PAGE'
export interface PageLink { type: LinkType; targetId: string }
export interface HotzoneArea { x: number; y: number; width: number; height: number; link: PageLink }
export interface Slide { assetId: string; link?: PageLink }
export type MosaicTemplate = 'TWO' | 'THREE' | 'FOUR' | 'FEATURED'
export interface MosaicItem { assetId: string; title?: string; link?: PageLink }
export interface PageMetadata { tags: string[]; share: { title: string; description: string; coverAssetId: string } }
export interface NavigationItem { title: string; assetId?: string; link: PageLink }
export interface ComponentAppearance { backgroundColor?: string; padding?: number; margin?: number; radius?: number }
export interface ProductCard { productId: string; name: string; priceFen: number; imageUrl: string; purchasable: boolean }
export interface CouponCard { id: string; title: string; kind: string; minGoodsFen: number; discountFen: number; productIds: string[]; productNames: string[]; redeemEligible: boolean; validFrom: string; validUntil: string; remainingQuantity: number; selfClaimLimit: number; selfClaimedCount: number; canClaim: boolean; claimState: string }
export type CouponData = Record<string, CouponCard[]>
export type ComponentData = Record<string, ProductCard[]>
export interface PageComponent {
  componentId: string
  type: ComponentType
  sortOrder: number
  visible: boolean
  appearance?: ComponentAppearance
  props: {
    subtitle?: string
    align?: 'LEFT' | 'CENTER'
    size?: 16 | 20 | 24
    ratio?: 'AUTO' | '1:1' | '16:9'
    items?: (NavigationItem | MosaicItem)[]
    template?: MosaicTemplate
    gap?: 0 | 4 | 8 | 12 | 16
    height?: 4 | 8 | 12 | 16 | 24 | 32 | 48 | 64 | 96
    columns?: 2 | 3 | 4
    source?: 'MANUAL' | 'CATEGORY' | 'AUTO'
    campaignIds?: string[]
    productIds?: string[]
    categoryId?: string
    limit?: number
    layout?: 'GRID' | 'LIST' | 'SCROLL'
    sort?: 'NEWEST' | 'PRICE_ASC'
    placeholder?: string
    text?: string
    link?: PageLink
    slides?: Slide[]
    assetId?: string
    areas?: HotzoneArea[]
    style?: 'SOLID' | 'DASHED' | 'SPACE'
    recordNo?: string
  }
}
export interface PageConfig {
  schemaVersion: 1 | 2 | 3 | 4
  pageType: 'HOME' | 'MICRO'
  theme: { pageBackgroundColor: string; headerBackgroundColor: string; brandTextColor: string }
  components: PageComponent[]
  metadata?: PageMetadata
}
export interface HomeDraft {
  pageId: string
  revision: number
  config: PageConfig
  publishedRevision?: number | null
  publishedVersionId?: string | null
  publicationRevision?: number
}
export interface HomePreview { revision: number; config: PageConfig; componentData?: ComponentData; couponData?: CouponData }
export interface HomePublication { versionId: string; revision: number; config: PageConfig }
export interface MicroPageSummary { pageId: string; name: string; revision: number; publishedRevision: number | null; updatedAt: string; tags?: string[] }
export interface MicroPageList { rows: MicroPageSummary[]; page: number; pageSize: number; total: number }
export interface MicroDraft extends HomeDraft { name: string }
export interface UploadedAssetBinding {
  componentId: string
  type: 'CAROUSEL' | 'IMAGE_HOTZONE' | 'IMAGE'
  slot: number | 'hotzone' | 'image'
  assetId: string
  expectedSlides?: string
  expectedAssetId?: string
}

export const componentNames: Record<ComponentType, string> = {
  SEARCH: '搜索框', NOTICE: '公告栏', CAROUSEL: '轮播图', IMAGE_HOTZONE: '图片热区',
  DIVIDER: '辅助线', FILING: '备案号', TITLE: '标题文本', IMAGE: '图片广告', NAVIGATION: '图文导航', PRODUCT_LIST: '商品列表', MOSAIC: '固定魔方', SPACER: '辅助空白', COUPON_LIST: '优惠券列表',
}

export function createComponent(type: ComponentType, order: number): PageComponent {
  const defaults: Record<ComponentType, PageComponent['props']> = {
    COUPON_LIST: { source: 'AUTO', campaignIds: [], limit: 3, layout: 'LIST' },
    MOSAIC: { template: 'TWO', items: [{ assetId: '' }, { assetId: '' }], gap: 8 },
    SPACER: { height: 16 },
    TITLE: { text: '', align: 'LEFT', size: 20 },
    IMAGE: { assetId: '', ratio: 'AUTO' },
    NAVIGATION: { items: [], columns: 4 },
    PRODUCT_LIST: { source: 'MANUAL', productIds: [], categoryId: '', limit: 6, layout: 'GRID', sort: 'NEWEST' },
    SEARCH: { placeholder: '搜索商品' },
    NOTICE: { text: '' },
    CAROUSEL: { slides: [] },
    IMAGE_HOTZONE: { assetId: '', areas: [] },
    DIVIDER: { style: 'SOLID' },
    FILING: { recordNo: '' },
  }
  return { componentId: crypto.randomUUID(), type, sortOrder: order, visible: true, props: defaults[type] }
}

export function normalizeConfig(input: PageConfig): PageConfig {
  if (![1, 2, 3, 4].includes(input.schemaVersion)) throw new Error('此页面配置需要更新管理端版本，请更新后重新打开。')
  return {
    ...(input.metadata ? { metadata: JSON.parse(JSON.stringify(input.metadata)) as PageMetadata } : {}),
    schemaVersion: input.schemaVersion === 4 || input.components.some(item => item.type === 'COUPON_LIST') ? 4 : input.schemaVersion === 3 || input.metadata || input.components.some(item => ['MOSAIC', 'SPACER'].includes(item.type)) ? 3 : input.schemaVersion === 2 || input.components.some(item => ['TITLE', 'IMAGE', 'NAVIGATION', 'PRODUCT_LIST'].includes(item.type) || item.appearance) ? 2 : 1,
    pageType: input.pageType,
    theme: { ...input.theme },
    components: input.components.map((item, index) => ({
      ...item,
      ...(item.appearance ? { appearance: { ...item.appearance } } : {}),
      sortOrder: index + 1,
      // Page props are JSON values; serializing also unwraps nested Vue proxies.
      props: JSON.parse(JSON.stringify(item.props)) as PageComponent['props'],
    })),
  }
}

export function applyUploadedAsset(config: PageConfig, upload: UploadedAssetBinding): PageConfig {
  const index = config.components.findIndex((item) => item.componentId === upload.componentId)
  if (index < 0) return config
  const component = config.components[index]
  if (component.type !== upload.type) return config
  let props: PageComponent['props']
  if (upload.slot === 'hotzone' || upload.slot === 'image') {
    if (component.type !== (upload.slot === 'image' ? 'IMAGE' : 'IMAGE_HOTZONE') || (component.props.assetId || '') !== upload.expectedAssetId) return config
    props = { ...component.props, assetId: upload.assetId }
  } else {
    const slides = component.props.slides || []
    if (component.type !== 'CAROUSEL' || !Number.isInteger(upload.slot)
      || !slides[upload.slot] || JSON.stringify(slides) !== upload.expectedSlides) return config
    props = { ...component.props, slides: slides.map((slide, position) =>
      position === upload.slot ? { ...slide, assetId: upload.assetId } : slide) }
  }
  const components = config.components.map((item, position) =>
    position === index ? { ...component, props } : item)
  return { ...config, components }
}
