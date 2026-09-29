import { afterEach, describe, expect, it } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { EditorContent } from '@tiptap/vue-3'
import ProductDescriptionEditor from '../../src/views/catalog/ProductDescriptionEditor.vue'

const a = '11111111-1111-4111-8111-111111111111'
const b = '22222222-2222-4222-8222-222222222222'
const asset = (id: string) => ({ assetId: id, kind: 'IMAGE', adminUrl: `/api/v1/admin/assets/${id}/file`, contentType: 'image/png', byteSize: 1 })
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
async function setup(extra: Record<string, unknown> = {}) {
  const wrapper = mount(ProductDescriptionEditor, { props: { modelValue: '<p>商品说明</p>', targetKey: 'account/product', ...extra,
    'onUpdate:modelValue': (value: string) => { void wrapper.setProps({ modelValue: value }) },
  }, global: { stubs: { AssetPicker: { name: 'AssetPicker', props: ['disabled', 'targetKey'], template: '<button :disabled="disabled">素材中心</button>' } } } })
  wrappers.push(wrapper); await flushPromises(); return wrapper
}
const editor = (w: ReturnType<typeof mount>) => w.getComponent(EditorContent).props('editor')!
async function button(w: ReturnType<typeof mount>, name: string) { await w.findAll('button').find(x => x.text() === name)!.trigger('click'); await flushPromises() }
function pick(w: ReturnType<typeof mount>, id: string) { w.getComponent({ name: 'AssetPicker' }).vm.$emit('select', asset(id)) }

describe('product description editor', () => {
  it('keeps incoming HTML clean and unchanged until a real edit, and formats headings and emphasis with undo', async () => {
    const w = await setup(); expect(w.emitted('update:modelValue')).toBeUndefined()
    expect(w.get('[role="textbox"]').attributes('aria-label')).toBe('商品描述')
    editor(w).commands.selectAll(); await button(w, '二级标题'); expect(w.props('modelValue')).toContain('<h2>')
    await button(w, '加粗'); expect(w.props('modelValue')).toContain('<strong>')
    await button(w, '撤销'); expect(w.props('modelValue')).not.toContain('<strong>')
    await button(w, '重做'); expect(w.props('modelValue')).toContain('<strong>')
  })
  it('inserts library images, edits accessible alt text and changes image order without losing prose', async () => {
    const w = await setup(); pick(w, a); await flushPromises(); pick(w, b); await flushPromises()
    expect(w.findAll('.description-image-row')).toHaveLength(2)
    await w.findAll('.description-image-row')[0]!.get('input').setValue('原产地说明')
    await w.findAll('.description-image-row')[1]!.findAll('button').find(x => x.text() === '前移')!.trigger('click')
    await flushPromises()
    const html = String(w.props('modelValue')); expect(html).toContain('商品说明'); expect(html.indexOf(b)).toBeLessThan(html.indexOf(a)); expect(html).toContain('alt="原产地说明"')
    await w.findAll('.description-image-row')[0]!.findAll('button').find(x => x.text() === '移除')!.trigger('click')
    expect(w.findAll('.description-image-row')).toHaveLength(1)
  })
  it('never trusts image URLs or event attributes from pasted HTML, and rejects images without asset identity', async () => {
    const w = await setup({ modelValue: `<p onclick="alert(1)">正文</p><img data-asset-id="${a}" src="https://untrusted.invalid/image" onerror="alert(1)"><img src="data:image/png;base64,a">` })
    const html = editor(w).getHTML(); expect(html).not.toMatch(/onclick|onerror|untrusted|base64/)
    expect(html).toContain(`/api/v1/admin/assets/${a}/file`)
    expect(w.findAll('.description-image-row')).toHaveLength(1)
    await button(w, '手机预览'); expect(w.get('.description-phone-preview img').attributes('src')).toBe(`/api/v1/admin/assets/${a}/file`)
  })
  it('disables editing and picker bindings, then destroys undo history on account or product switch', async () => {
    const w = await setup(); pick(w, a); await flushPromises(); await w.setProps({ disabled: true })
    pick(w, b); await flushPromises(); expect(w.findAll('.description-image-row')).toHaveLength(1)
    expect(w.get('[role="textbox"]').attributes('contenteditable')).toBe('false')
    await w.setProps({ targetKey: 'other/other', modelValue: '<p>另一商品</p>', disabled: false })
    await flushPromises()
    expect(w.text()).toContain('另一商品'); expect(editor(w).can().undo()).toBe(false)
    expect(w.findAll('.description-image-row')).toHaveLength(0)
  })
  it('blocks a 21st image and over-limit text while preserving the last valid content', async () => {
    const w = await setup({ modelValue: Array.from({ length: 20 }, () => `<img data-asset-id="${a}">`).join('') })
    pick(w, b); await flushPromises(); expect(w.findAll('.description-image-row')).toHaveLength(20)
    expect(w.text()).toContain('最多 20 张')
    editor(w).commands.insertContent('x'.repeat(20001)); await flushPromises()
    expect(editor(w).getHTML()).not.toContain('x'.repeat(20001)); expect(w.text()).toContain('20000')
  })
  it('supports paragraphs, both lists and italic while refusing direct pasted or dropped files', async () => {
    const w = await setup(); editor(w).commands.selectAll()
    await button(w, '三级标题'); expect(editor(w).getHTML()).toContain('<h3>')
    await button(w, '斜体'); expect(editor(w).getHTML()).toContain('<em>')
    await button(w, '正文'); expect(editor(w).getHTML()).toContain('<p>')
    await button(w, '无序列表'); expect(editor(w).getHTML()).toContain('<ul>')
    await button(w, '有序列表'); expect(editor(w).getHTML()).toContain('<ol>')
    const e = editor(w)
    const paste = e.options.editorProps.handlePaste!, drop = e.options.editorProps.handleDrop!
    expect(paste.call(e.view, e.view, { clipboardData: { files: [new File(['x'], 'x.png')] } } as unknown as ClipboardEvent, null as never)).toBe(true)
    expect(drop.call(e.view, e.view, { dataTransfer: { files: [new File(['x'], 'x.png')] } } as unknown as DragEvent, null as never, false)).toBe(true)
    await flushPromises(); expect(w.text()).toContain('请通过素材中心上传')
    expect(paste.call(e.view, e.view, { clipboardData: { files: [] } } as unknown as ClipboardEvent, null as never)).toBe(false)
    expect(drop.call(e.view, e.view, { dataTransfer: { files: [] } } as unknown as DragEvent, null as never, false)).toBe(false)
  })
  it('rejects wrong media types and malformed identities without emitting, supports later image move', async () => {
    const w = await setup()
    w.getComponent({ name: 'AssetPicker' }).vm.$emit('select', { ...asset(a), kind: 'VIDEO' })
    await flushPromises(); expect(w.text()).toContain('有效图片')
    pick(w, 'not-a-uuid'); await flushPromises(); expect(w.emitted('update:modelValue')).toBeUndefined()
    pick(w, a); await flushPromises(); pick(w, b); await flushPromises()
    await w.findAll('.description-image-row')[0]!.findAll('button').find(x => x.text() === '后移')!.trigger('click')
    await flushPromises(); expect(w.findAll('.description-image-row')[0]!.get('img').attributes('src')).toContain(b)
    expect(String(w.props('modelValue'))).not.toContain('src=')
    editor(w).commands.clearContent(); await flushPromises(); expect(w.props('modelValue')).toBe('')
  })
  it('keeps typed Markdown image links as text rather than creating an unbound asset node', async () => {
    const w = await setup({ modelValue: '<p>![外链](https://outside.invalid/photo</p>' })
    const e = editor(w); e.commands.setTextSelection(e.state.doc.content.size - 1)
    const position = e.state.selection.from
    e.view.someProp('handleTextInput', handler => handler(e.view, position, position, ')', () => e.state.tr.insertText(')')))
    await flushPromises()
    expect(w.findAll('.description-image-row')).toHaveLength(0)
    expect(e.getHTML()).not.toContain('<img')
  })
  it('responds to server HTML updates without making an unchanged form dirty', async () => {
    const w = await setup(); await w.setProps({ modelValue: '<h3>服务端保存结果</h3>' })
    expect(editor(w).getHTML()).toBe('<h3>服务端保存结果</h3>')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})
