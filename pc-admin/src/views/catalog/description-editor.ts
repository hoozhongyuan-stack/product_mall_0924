import { Extension, type JSONContent } from '@tiptap/core'
import Document from '@tiptap/extension-document'
import Paragraph from '@tiptap/extension-paragraph'
import Text from '@tiptap/extension-text'
import Heading from '@tiptap/extension-heading'
import Bold from '@tiptap/extension-bold'
import Italic from '@tiptap/extension-italic'
import HardBreak from '@tiptap/extension-hard-break'
import { BulletList, OrderedList, ListItem, ListKeymap } from '@tiptap/extension-list'
import Image from '@tiptap/extension-image'
import { UndoRedo } from '@tiptap/extensions'
import { Plugin } from '@tiptap/pm/state'
import { DOMSerializer, type Node as ProseMirrorNode } from '@tiptap/pm/model'

export const DESCRIPTION_LIMIT = 20000
export const DESCRIPTION_IMAGE_LIMIT = 20
export const isAssetId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
export const assetPreview = (id: string) => `/api/v1/admin/assets/${id}/file`

// Only server-owned asset identities can create image nodes. Never render a pasted URL.
const AssetImage = Image.extend({
  // Image's default Markdown rule accepts arbitrary URLs without an asset identity.
  addInputRules() { return [] },
  addAttributes() {
    return {
      assetId: { default: null, parseHTML: element => element.getAttribute('data-asset-id'), rendered: false },
      alt: { default: '', parseHTML: element => Array.from(element.getAttribute('alt') || '').slice(0, 200).join(''), rendered: false },
    }
  },
  parseHTML() { return [{ tag: 'img[data-asset-id]', getAttrs: element => isAssetId(element.getAttribute('data-asset-id')) ? null : false }] },
  renderHTML({ node }) {
    return ['img', { 'data-asset-id': node.attrs.assetId, alt: node.attrs.alt, src: assetPreview(node.attrs.assetId) }]
  },
}).configure({ allowBase64: false })

export function descriptionHtml(doc: ProseMirrorNode) {
  const element = document.createElement('div')
  element.append(DOMSerializer.fromSchema(doc.type.schema).serializeFragment(doc.content))
  element.querySelectorAll('img').forEach(image => image.removeAttribute('src'))
  return element.innerHTML
}
export function descriptionImages(doc: ProseMirrorNode) {
  const images: { position: number; assetId: string; alt: string }[] = []
  doc.descendants((node, position) => { if (node.type.name === 'image') images.push({ position, assetId: node.attrs.assetId, alt: node.attrs.alt }) })
  return images
}
export function descriptionIssue(doc: ProseMirrorNode) {
  if (descriptionImages(doc).length > DESCRIPTION_IMAGE_LIMIT) return '商品描述最多 20 张图片，请先移除部分图片。'
  if (Array.from(descriptionHtml(doc)).length > DESCRIPTION_LIMIT) return '商品描述内容已超过 20000 字符，请缩短文字或减少图片。'
  return ''
}
export const imageContent = (id: string): JSONContent => ({ type: 'image', attrs: { assetId: id, alt: '' } })
export function descriptionExtensions(rejected: (message: string) => void) {
  return [Document, Paragraph, Text, Heading.configure({ levels: [2, 3] }), Bold, Italic, HardBreak,
    BulletList, OrderedList, ListItem, ListKeymap, AssetImage, UndoRedo,
    Extension.create({ name: 'descriptionLimits', addProseMirrorPlugins() {
      return [new Plugin({ filterTransaction(transaction) {
        if (!transaction.docChanged) return true
        const issue = descriptionIssue(transaction.doc)
        if (issue) rejected(issue)
        return !issue
      } })]
    } })]
}
