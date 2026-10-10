<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue'
import { Editor, EditorContent } from '@tiptap/vue-3'
import { ElButton } from 'element-plus'
import AssetPicker from '../../shared/AssetPicker.vue'
import type { Asset } from '../../shared/media'
import { assetPreview, descriptionExtensions, descriptionHtml, descriptionImages, imageContent, isAssetId } from './description-editor'
import './description-editor.css'

const props = defineProps<{ modelValue: string; targetKey: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const editor = shallowRef<Editor>()
const revision = ref(0)
const error = ref('')
const preview = ref(false)
const titleId = useId()
const images = computed(() => { void revision.value; return editor.value ? descriptionImages(editor.value.state.doc) : [] })
const html = computed(() => { void revision.value; return editor.value?.getHTML() || '' })
const tools = [
  { label: '正文', type: 'paragraph' }, { label: '二级标题', type: 'h2' }, { label: '三级标题', type: 'h3' },
  { label: '加粗', type: 'bold' }, { label: '斜体', type: 'italic' },
  { label: '无序列表', type: 'bulletList' }, { label: '有序列表', type: 'orderedList' },
] as const
function buildEditor() {
  editor.value?.destroy()
  error.value = ''; preview.value = false
  editor.value = new Editor({
    extensions: descriptionExtensions(message => { error.value = message }),
    content: props.modelValue, editable: !props.disabled,
    editorProps: { attributes: { role: 'textbox', 'aria-label': '商品描述', 'aria-multiline': 'true', class: 'description-prose' },
      handleDrop: (_view, event) => {
        if (!event.dataTransfer?.files.length) return false
        error.value = '请通过素材中心上传并选择图片。'; return true
      },
      handlePaste: (_view, event) => {
        if (!event.clipboardData?.files.length) return false
        error.value = '请通过素材中心上传并选择图片。'; return true
      },
    },
    onTransaction: () => { revision.value++ },
    onUpdate: ({ editor: current }) => {
      if (props.disabled) return
      error.value = ''
      emit('update:modelValue', current.isEmpty ? '' : descriptionHtml(current.state.doc))
    },
  })
  revision.value++
}
onMounted(buildEditor)
watch(() => props.targetKey, buildEditor, { flush: 'post' })
watch(() => props.modelValue, value => {
  const current = editor.value
  if (current && value !== (current.isEmpty ? '' : descriptionHtml(current.state.doc))) current.commands.setContent(value, { emitUpdate: false })
})
watch(() => props.disabled, value => editor.value?.setEditable(!value, false))
onBeforeUnmount(() => editor.value?.destroy())
function active(type: string) {
  void revision.value
  return type === 'h2' || type === 'h3' ? editor.value?.isActive('heading', { level: Number(type[1]) }) : editor.value?.isActive(type)
}
function format(type: string) {
  if (props.disabled || !editor.value) return
  const command = editor.value.chain()
  if (type === 'paragraph') command.setParagraph().run()
  else if (type === 'h2' || type === 'h3') command.toggleHeading({ level: Number(type[1]) as 2 | 3 }).run()
  else if (type === 'bold') command.toggleBold().run()
  else if (type === 'italic') command.toggleItalic().run()
  else if (type === 'bulletList') command.toggleBulletList().run()
  else command.toggleOrderedList().run()
  // Vue's native toolbar can restore the committed selection immediately.
  // Tiptap focus() queues a frame that may arrive after the user moves on.
  editor.value.view.focus()
}
function history(action: 'undo' | 'redo') {
  if (props.disabled || !editor.value) return
  editor.value.chain()[action]().run()
  editor.value.view.focus()
}
function canHistory(action: 'undo' | 'redo') { void revision.value; return !!editor.value?.can()[action]() }
function select(asset: Asset) {
  if (props.disabled || !editor.value) return
  if (asset.kind !== 'IMAGE' || !isAssetId(asset.assetId)) { error.value = '请选择素材中心中的有效图片。'; return }
  if (images.value.length >= 20) { error.value = '商品描述最多 20 张图片，请先移除部分图片。'; return }
  // Keep the document selection without scheduling a later focus that can steal
  // typing from the image description field after the picker closes.
  editor.value.chain().insertContent(imageContent(asset.assetId)).run()
}
function editImage(index: number, action: 'remove' | 'before' | 'after' | 'alt', value = '') {
  const current = editor.value, image = images.value[index]
  if (props.disabled || !current || !image) return
  const transaction = current.state.tr
  if (action === 'remove') transaction.delete(image.position, image.position + 1)
  else if (action === 'alt') transaction.setNodeMarkup(image.position, undefined, { assetId: image.assetId, alt: Array.from(value).slice(0, 200).join('') })
  else {
    const other = images.value[index + (action === 'before' ? -1 : 1)]
    if (!other) return
    transaction.setNodeMarkup(image.position, undefined, { assetId: other.assetId, alt: other.alt })
    transaction.setNodeMarkup(other.position, undefined, { assetId: image.assetId, alt: image.alt })
  }
  current.view.dispatch(transaction)
}
</script>
<template>
  <section class="product-description-editor" :aria-labelledby="titleId">
    <div class="description-heading"><div><h3 :id="titleId">商品描述</h3><p>用文字和图片介绍商品，保存后展示在小程序详情中。</p></div><ElButton :aria-pressed="preview" @click="preview = !preview">{{ preview ? '收起预览' : '手机预览' }}</ElButton></div>
    <div class="description-toolbar" role="group" aria-label="商品描述格式">
      <ElButton v-for="tool in tools" :key="tool.type" :disabled="disabled" :aria-pressed="!!active(tool.type)" @click="format(tool.type)">{{ tool.label }}</ElButton>
      <ElButton :disabled="disabled || !canHistory('undo')" @click="history('undo')">撤销</ElButton><ElButton :disabled="disabled || !canHistory('redo')" @click="history('redo')">重做</ElButton>
    </div>
    <div class="description-composition" :class="{ 'has-preview': preview }">
      <div class="description-input"><EditorContent :editor="editor" /><p v-if="editor?.isEmpty" class="description-empty">添加商品特点、使用方法或图文介绍。</p></div>
      <aside v-if="preview" class="description-preview" aria-label="手机宽度预览"><p>手机宽度预览 · 实际展示以小程序为准</p><div class="description-phone-preview description-prose" v-html="html" /></aside>
    </div>
    <p v-if="error" class="error description-error" role="alert">{{ error }}</p>
    <div class="description-image-heading"><h4>描述图片 <span>{{ images.length }} / 20</span></h4><AssetPicker :key="targetKey" kind="IMAGE" :disabled="disabled || images.length >= 20" :target-key="JSON.stringify([targetKey, 'description'])" @select="select" /></div>
    <p class="description-help">从素材中心选择或上传图片，插入到光标处；支持 JPG、PNG。图片前后移动只调整图片顺序，文字位置不变。</p>
    <ol v-if="images.length" class="description-image-list">
      <li v-for="(image, index) in images" :key="`${image.position}:${image.assetId}`" class="description-image-row">
        <img :src="assetPreview(image.assetId)" :alt="image.alt || `描述图片 ${index + 1}`" />
        <label>图片 {{ index + 1 }} 说明<input :value="image.alt" :disabled="disabled" maxlength="200" placeholder="可选，帮助用户理解图片" @input="editImage(index, 'alt', ($event.target as HTMLInputElement).value)" /></label>
        <div class="description-image-actions"><ElButton :disabled="disabled || index === 0" @click="editImage(index, 'before')">前移</ElButton><ElButton :disabled="disabled || index === images.length - 1" @click="editImage(index, 'after')">后移</ElButton><ElButton :disabled="disabled" @click="editImage(index, 'remove')">移除</ElButton></div>
      </li>
    </ol>
  </section>
</template>
