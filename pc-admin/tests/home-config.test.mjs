import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { reactive } from 'vue'
import ts from 'typescript'

const source = readFileSync(new URL('../src/views/pages/types.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { normalizeConfig, applyUploadedAsset } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('normalizes nested Vue reactive component props into independent plain JSON', () => {
  const input = reactive({
    schemaVersion: 1,
    pageType: 'HOME',
    theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#254b37', brandTextColor: '#ffffff' },
    components: [{ componentId: 'notice', type: 'NOTICE', sortOrder: 10, visible: true,
      props: { text: '今日营业', link: { type: 'FUNCTION', targetId: 'CATALOG' } } }],
  })
  const result = normalizeConfig(input)
  assert.equal(result.components[0].sortOrder, 1)
  assert.deepEqual(result.components[0].props, { text: '今日营业', link: { type: 'FUNCTION', targetId: 'CATALOG' } })
  assert.equal(Object.getPrototypeOf(result.components[0].props), Object.prototype)
  input.components[0].props.link.targetId = 'SEARCH'
  assert.equal(result.components[0].props.link.targetId, 'CATALOG')
})

test('normalizes an independent micro page without turning it into a home page', () => {
  const input = { schemaVersion: 1, pageType: 'MICRO',
    theme: { pageBackgroundColor: '#fff5e8', headerBackgroundColor: '#b63f32', brandTextColor: '#fff8ef' },
    components: [{ componentId: 'notice', type: 'NOTICE', sortOrder: 8, visible: true,
      props: { text: '活动须知', link: { type: 'PAGE', targetId: '00000000-0000-4000-8000-000000000001' } } }],
  }
  const result = normalizeConfig(input)
  assert.equal(result.pageType, 'MICRO')
  assert.equal(result.components[0].sortOrder, 1)
  assert.deepEqual(result.components[0].props.link, input.components[0].props.link)
})

test('late upload updates its original component even after the selected component changes', () => {
  const original = reactive({
    schemaVersion: 1, pageType: 'HOME',
    theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#254b37', brandTextColor: '#ffffff' },
    components: [
      { componentId: 'carousel-a', type: 'CAROUSEL', sortOrder: 1, visible: true, props: { slides: [{ assetId: '' }] } },
      { componentId: 'carousel-b', type: 'CAROUSEL', sortOrder: 2, visible: true, props: { slides: [{ assetId: '' }] } },
    ],
  })
  const upload = { componentId: 'carousel-a', type: 'CAROUSEL', slot: 0,
    expectedSlides: JSON.stringify(original.components[0].props.slides), assetId: 'uploaded-image' }
  const afterSwitch = applyUploadedAsset(original, upload)
  assert.equal(afterSwitch.components[0].props.slides[0].assetId, 'uploaded-image')
  assert.equal(afterSwitch.components[1].props.slides[0].assetId, '')

  const afterDelete = applyUploadedAsset({ ...original, components: [original.components[1]] }, upload)
  assert.equal(afterDelete.components[0].props.slides[0].assetId, '')
})

test('late upload does not bind to a different slide after its position changes', () => {
  const original = { schemaVersion: 1, pageType: 'HOME',
    theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#254b37', brandTextColor: '#ffffff' },
    components: [{ componentId: 'carousel-a', type: 'CAROUSEL', sortOrder: 1, visible: true,
      props: { slides: [{ assetId: 'first' }, { assetId: 'second' }] } }],
  }
  const upload = { componentId: 'carousel-a', type: 'CAROUSEL', slot: 1,
    expectedSlides: JSON.stringify(original.components[0].props.slides), assetId: 'uploaded-image' }
  const afterDelete = { ...original, components: [{ ...original.components[0], props: { slides: [{ assetId: 'second' }] } }] }
  assert.equal(applyUploadedAsset(afterDelete, upload), afterDelete)
})
