// Browser execution bridge for real native source. No business actions are implemented here.
const config = await fetch('/preview/config').then(response => response.json())
const storage = new Map([['mall.memberToken', config.memberToken]])
const files = new Map(), modules = new Map(), sourceCache = new Map()
const host = document.querySelector('#native-page')
let page, markup, nativeStyle, mounting = false
const templates = new Map()
let mountToken = 0
window.getApp = () => ({ globalData: { apiBaseUrl: location.origin } })
window.wx = {
  getStorageSync: key => key.startsWith('mall.storeFulfillmentPending.') ? JSON.parse(localStorage.getItem(key) || 'null') : storage.get(key),
  setStorageSync: (key, value) => key.startsWith('mall.storeFulfillmentPending.') ? localStorage.setItem(key, JSON.stringify(value)) : storage.set(key, value),
  removeStorageSync: key => key.startsWith('mall.storeFulfillmentPending.') ? localStorage.removeItem(key) : storage.delete(key),
  request(options) {
    fetch(options.url, { method: options.method, headers: options.header, ...(options.data ? { body: JSON.stringify(options.data) } : {}) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(() => options.fail())
  },
  showModal(options) {
    const dialog = document.querySelector('#confirmation')
    dialog.querySelector('h3').textContent = options.title; dialog.querySelector('p').textContent = options.content
    const buttons = dialog.querySelectorAll('button'); buttons[0].textContent = options.cancelText || '取消'; buttons[1].textContent = options.confirmText || '确认'
    const close = event => { const confirm = event.currentTarget.value === 'true'; dialog.close(); buttons.forEach(button => button.onclick = null); options.success({ confirm }) }
    buttons.forEach(button => button.onclick = close); dialog.oncancel = () => options.success({ confirm: false }); dialog.showModal()
  },
  showToast: options => { document.querySelector('#preview-error').textContent = options.title },
  stopPullDownRefresh() {}, enableAlertBeforeUnload() {}, disableAlertBeforeUnload() {},
  setClipboardData: options => navigator.clipboard.writeText(options.data).then(() => options.success()).catch(() => options.fail()),
  navigateTo: options => { const url = new URL(options.url, location.origin); const id = url.searchParams.get('id'); if (id) document.querySelector('#order').value = id; document.querySelector('#role').value = url.pathname.includes('store-center/order') ? 'warehouse' : 'customer'; mount() },
  openLocation: options => options.fail?.(), makePhoneCall: options => options.fail?.(),
  env: { USER_DATA_PATH: 'wxfile://usr' },
  getFileSystemManager: () => ({ writeFile(options) { files.set(options.filePath, `data:image/png;base64,${options.data}`); options.success() }, unlink(options) { files.delete(options.filePath); options.success?.() }, readdir(options) { options.success({ files: [] }) } }),
}
window.Page = definition => { page = { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch }; if (!mounting) render() } } }
async function source(path) { if (!sourceCache.has(path)) { const response = await fetch(path); if (!response.ok) throw new Error(`源码不可读取：${path}`); sourceCache.set(path, await response.text()) } return sourceCache.get(path) }
function resolvePath(from, target) { const path = new URL(target, `${location.origin}${from}`).pathname; return path.endsWith('.js') ? path : `${path}.js` }
async function prepare(path) {
  const seen = new Set()
  let pending = [path]
  while (pending.length) {
    const current = pending.filter(file => !seen.has(file)); current.forEach(file => seen.add(file))
    const contents = await Promise.all(current.map(file => source(file)))
    pending = contents.flatMap((content, index) => [...content.matchAll(/require\(['"]([^'"]+)['"]\)/g)].map(match => resolvePath(current[index], match[1])))
  }
}
function requireSource(path) {
  if (modules.has(path)) return modules.get(path).exports
  const module = { exports: {} }; modules.set(path, module)
  Function('require', 'module', 'exports', sourceCache.get(path))(target => requireSource(resolvePath(path, target)), module, module.exports)
  return module.exports
}
function expression(value, scope) {
  try { return Function('data', `with(data){return (${value})}`)(scope) } catch { return undefined }
}
function interpolate(text, scope) { return text.replace(/{{([\s\S]*?)}}/g, (_, value) => { const result = expression(value, scope); return result == null ? '' : String(result) }) }
function valueOf(text, scope) { const match = text.match(/^{{([\s\S]*?)}}$/); return match ? expression(match[1], scope) : interpolate(text, scope) }
function appendChildren(parent, nodes, scope) {
  let branch = null
  for (const node of nodes) {
    if (node.nodeType !== 1) { if (node.nodeType === 3 && node.textContent.trim()) parent.append(document.createTextNode(interpolate(node.textContent, scope))); continue }
    const has = key => node.hasAttribute(key)
    if (has('wx:if')) { branch = !!valueOf(node.getAttribute('wx:if'), scope); if (!branch) continue }
    else if (has('wx:elif')) { if (branch || !valueOf(node.getAttribute('wx:elif'), scope)) continue; branch = true }
    else if (has('wx:else')) { if (branch) continue; branch = true }
    else branch = null
    if (has('wx:for')) {
      const values = valueOf(node.getAttribute('wx:for'), scope) || []
      Array.from(values).forEach((item, index) => {
        const clone = node.cloneNode(true); clone.removeAttribute('wx:for'); clone.removeAttribute('wx:if')
        const local = { ...scope, [node.getAttribute('wx:for-item') || 'item']: item, [node.getAttribute('wx:for-index') || 'index']: index }
        appendChildren(parent, [clone], local)
      }); continue
    }
    const tag = node.tagName
    if (tag === 'import') continue
    if (tag === 'template') {
      const template = templates.get(node.getAttribute('is'))
      if (template) { const data = (node.getAttribute('data') || '').replace(/^{{|}}$/g, ''); appendChildren(parent, [...template.childNodes], { ...scope, ...(expression(`{${data}}`, scope) || {}) }) }
      continue
    }
    if (tag === 'block') { appendChildren(parent, [...node.childNodes], scope); continue }
    const element = document.createElement(({ view: 'div', text: 'span', image: 'img', 'scroll-view': 'div', 'checkbox-group': 'div', checkbox: 'input' })[tag] || tag)
    if (tag === 'checkbox') element.type = 'checkbox'
    for (const attribute of node.attributes) {
      const name = attribute.name
      if (name.startsWith('wx:') || ['bindtap', 'bindinput', 'bindchange', 'binderror'].includes(name)) continue
      const value = valueOf(attribute.value, scope)
      if (['disabled', 'checked'].includes(name)) { element[name] = !!value; continue }
      if (name === 'value' && ['input', 'textarea'].includes(element.tagName.toLowerCase())) { element.value = value ?? ''; continue }
      if (name === 'src') { element.setAttribute(name, files.get(value) || (String(value).startsWith('/assets/') ? `/mini${value}` : value)); continue }
      element.setAttribute(name, value == null ? '' : String(value))
    }
    if (tag === 'image') element.style.objectFit = 'contain'
    for (const [attribute, eventType] of [['bindtap', 'click'], ['bindinput', 'input'], ['bindchange', 'change'], ['binderror', 'error']]) {
      const method = node.getAttribute(attribute)
      if (method) element.addEventListener(eventType, event => {
        event.stopPropagation()
        const value = tag === 'checkbox-group' ? [...element.querySelectorAll('input:checked')].map(input => input.value) : event.target.value
        const result = page[method]?.({ currentTarget: { dataset: { ...element.dataset } }, detail: { value }, target: { dataset: { ...event.target.dataset } } })
        Promise.resolve(result).catch(error => { document.querySelector('#preview-error').textContent = error.stack })
      })
    }
    appendChildren(element, [...node.childNodes], scope); parent.append(element)
  }
}
function parseMarkup(text) { return new DOMParser().parseFromString(`<root xmlns:wx="urn:wechat">${text.replace(/&(?!amp;|lt;|gt;|quot;|apos;)/g, '&amp;').replace(/{{[\s\S]*?}}/g, value => value.replace(/</g, '&lt;')).replace(/\b(wx:else|selectable|scroll-x|scroll-y|enhanced|show-scrollbar)(?=\s|>)/g, '$1=""')}</root>`, 'application/xml') }
function render() {
  const active = document.activeElement, focus = active && host.contains(active) ? { dataset: { ...active.dataset }, start: active.selectionStart, end: active.selectionEnd } : null
  const xml = parseMarkup(markup)
  const error = xml.querySelector('parsererror'); if (error) throw new Error(error.textContent)
  host.replaceChildren(); appendChildren(host, [...xml.documentElement.childNodes], page.data)
  if (focus) {
    const replacement = [...host.querySelectorAll('input,textarea')].find(element => Object.entries(focus.dataset).every(([key, value]) => element.dataset[key] === value))
    if (replacement) { replacement.focus(); try { replacement.setSelectionRange(focus.start, focus.end) } catch {} }
  }
}
async function css(path, seen = new Set()) {
  if (seen.has(path)) return ''; seen.add(path)
  let content = await source(path)
  const imports = [...content.matchAll(/@import\s+['"]([^'"]+)['"];?/g)]
  const imported = await Promise.all(imports.map(match => css(new URL(match[1], `${location.origin}${path}`).pathname, seen)))
  content = content.replace(/@import\s+['"][^'"]+['"];?/g, '').replace(/(-?[\d.]+)rpx/g, (_, number) => `${Number(number) / 2}px`).replace(/([^{}]+)\{/g, (_, selectors) => `${selectors.replace(/(?<![.\w-])(page|view|text|image|scroll-view|checkbox-group)(?![\w-])/g, tag => ({ page: ':root', view: 'div', text: 'span', image: 'img', 'scroll-view': 'div', 'checkbox-group': 'div' })[tag])}{`)
  return imported.join('\n') + content
}
async function mount() {
  const token = ++mountToken
  page?.onUnload?.(); mounting = true
  document.querySelector('#preview-error').textContent = ''
  const role = document.querySelector('#role').value, orderId = document.querySelector('#order').value
  const path = role === 'dashboard' ? '/mini/pages/store-center/index' : role === 'warehouse' ? '/mini/pages/store-center/order' : '/mini/pages/orders/detail'
  const localMarkup = await source(`${path}.wxml`)
  if (token !== mountToken) return
  markup = localMarkup; templates.clear()
  for (const include of parseMarkup(markup).querySelectorAll('import')) {
    const included = parseMarkup(await source(new URL(include.getAttribute('src'), `${location.origin}${path}.wxml`).pathname))
    for (const template of included.querySelectorAll('template[name]')) templates.set(template.getAttribute('name'), template)
  }
  nativeStyle = await Promise.all([css('/mini/app.wxss'), css(`${path}.wxss`)])
  if (token !== mountToken) return
  document.querySelector('#native-style').textContent = nativeStyle.join('\n')
  await prepare(`${path}.js`); if (token !== mountToken) return
  modules.clear(); requireSource(`${path}.js`)
  document.querySelector('#page-title').textContent = role === 'dashboard' ? '前置仓中心' : '订单详情'
  mounting = false; render()
  if (role === 'dashboard') await page.onShow()
  else await page.onLoad({ id: orderId, storeId: config.storeId })
  window.previewPage = page
}
for (const order of config.orders) { const option = document.createElement('option'); option.value = order.orderId; option.textContent = order.name || order.orderNo || order.orderId; document.querySelector('#order').append(option) }
document.querySelector('#role').onchange = mount; document.querySelector('#order').onchange = mount
document.querySelector('#refresh').onclick = () => page.refresh?.() || page.load?.()
await mount()
