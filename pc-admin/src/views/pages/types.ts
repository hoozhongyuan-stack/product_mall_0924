export type ComponentType = 'SEARCH' | 'NOTICE' | 'CAROUSEL' | 'IMAGE_HOTZONE' | 'DIVIDER' | 'FILING'
export type LinkType = 'PRODUCT' | 'CATEGORY' | 'FUNCTION' | 'PAGE'
export interface PageLink { type: LinkType; targetId: string }
export interface HotzoneArea { x: number; y: number; width: number; height: number; link: PageLink }
export interface Slide { assetId: string; link?: PageLink }
export interface PageComponent {
  componentId: string
  type: ComponentType
  sortOrder: number
  visible: boolean
  props: {
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
  schemaVersion: 1
  pageType: 'HOME' | 'MICRO'
  theme: { pageBackgroundColor: string; headerBackgroundColor: string; brandTextColor: string }
  components: PageComponent[]
}
export interface HomeDraft {
  pageId: string
  revision: number
  config: PageConfig
  publishedRevision?: number | null
  publishedVersionId?: string | null
  publicationRevision?: number
}
export interface HomePreview { revision: number; config: PageConfig }
export interface HomePublication { versionId: string; revision: number; config: PageConfig }
export interface MicroPageSummary { pageId: string; name: string; revision: number; publishedRevision: number | null; updatedAt: string }
export interface MicroPageList { rows: MicroPageSummary[]; page: number; pageSize: number; total: number }
export interface MicroDraft extends HomeDraft { name: string }
export interface UploadedAssetBinding {
  componentId: string
  type: 'CAROUSEL' | 'IMAGE_HOTZONE'
  slot: number | 'hotzone'
  assetId: string
  expectedSlides?: string
  expectedAssetId?: string
}

export const componentNames: Record<ComponentType, string> = {
  SEARCH: '搜索框', NOTICE: '公告栏', CAROUSEL: '轮播图', IMAGE_HOTZONE: '图片热区',
  DIVIDER: '辅助线', FILING: '备案号',
}

export function createComponent(type: ComponentType, order: number): PageComponent {
  const defaults: Record<ComponentType, PageComponent['props']> = {
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
  return {
    schemaVersion: 1,
    pageType: input.pageType,
    theme: { ...input.theme },
    components: input.components.map((item, index) => ({
      ...item,
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
  if (upload.slot === 'hotzone') {
    if (component.type !== 'IMAGE_HOTZONE' || (component.props.assetId || '') !== upload.expectedAssetId) return config
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
