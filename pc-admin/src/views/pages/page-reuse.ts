import { normalizeConfig, type PageComponent, type PageConfig } from './types'
export interface PageTemplate { templateId: 'BRAND_HOME' | 'CATEGORY_GUIDE' | 'CAMPAIGN'; version: 1; name: string; description: string; config: PageConfig }
export interface PageCombination { combinationId: 'BRAND_HEADER' | 'CATEGORY_SECTION' | 'CAMPAIGN_ENTRY'; version: 1; name: string; description: string; components: PageComponent[] }
export interface PageReuseData { templates: PageTemplate[]; combinations: PageCombination[] }
export const themePresets = [
  { id: 'brandred', name: '品牌红', theme: { pageBackgroundColor: '#FFF2F0', headerBackgroundColor: '#A3202B', brandTextColor: '#FFFFFF' } },
  { id: 'cleanwhite', name: '清爽白', theme: { pageBackgroundColor: '#F7F7F7', headerBackgroundColor: '#FFFFFF', brandTextColor: '#262626' } },
  { id: 'dark', name: '深色', theme: { pageBackgroundColor: '#F4F5F5', headerBackgroundColor: '#20252A', brandTextColor: '#FFFFFF' } },
]
export function cloneComponents(items: PageComponent[], start = 1): PageComponent[] {
  return items.map((item, index) => ({ ...JSON.parse(JSON.stringify(item)) as PageComponent, componentId: crypto.randomUUID(), sortOrder: start + index }))
}
export function replaceWithTemplate(config: PageConfig, template: PageTemplate): PageConfig {
  return normalizeConfig({ ...JSON.parse(JSON.stringify(template.config)), pageType: config.pageType, ...(config.metadata ? { metadata: config.metadata } : {}), components: cloneComponents(template.config.components) })
}
export function validateReuseData(value: PageReuseData): PageReuseData {
  if (!value || !Array.isArray(value.templates) || !Array.isArray(value.combinations) || value.templates.length > 3 || value.combinations.length > 3 ||
    value.templates.some(item => !['BRAND_HOME', 'CATEGORY_GUIDE', 'CAMPAIGN'].includes(item.templateId) || item.version !== 1 || typeof item.name !== 'string' || typeof item.description !== 'string' || !item.config || item.config.schemaVersion !== 3 || !Array.isArray(item.config.components) || item.config.components.length > 40) ||
    value.combinations.some(item => !['BRAND_HEADER', 'CATEGORY_SECTION', 'CAMPAIGN_ENTRY'].includes(item.combinationId) || item.version !== 1 || typeof item.name !== 'string' || typeof item.description !== 'string' || !Array.isArray(item.components) || item.components.length > 40)) throw new Error('模板资料格式不正确。')
  return value
}
