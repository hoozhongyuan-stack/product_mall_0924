import { test, expect } from '@playwright/test'
import { randomUUID } from 'node:crypto'

test('真实隔离数据库：采购核款、发货、整单幂等收货保留销售占用', async ({ page, playwright }) => {
  test.setTimeout(90_000)
  const member = await playwright.request.newContext({ baseURL: process.env.MALL_E2E_BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MALL_E2E_MEMBER_TOKEN}` } })
  const anonymous = await playwright.request.newContext({ baseURL: process.env.MALL_E2E_BASE_URL })
  const base = `/api/v1/app/store-center/stores/${process.env.MALL_E2E_STORE_ID}/procurement`
  try {
    await page.goto('/')
    await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
    await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
    const csrf = (await page.context().cookies()).find(c => c.name === 'csrftoken')!.value
    const adminHeaders = { 'X-CSRFToken': csrf }
    const current = (await (await page.request.get('/api/v1/admin/stores/procurement/settings')).json()).data
    const settings = await page.request.put('/api/v1/admin/stores/procurement/settings', {
      headers: adminHeaders,
      data: { paymentInstructions: 'synthetic-procurement-only：隔离测试账户', expectedRevision: current.revision } })
    expect(settings.status()).toBe(200)
    const products = (await (await member.get(`${base}/products`)).json()).data.items
    expect(products).toHaveLength(1)
    const created = await member.post(`${base}/orders`, { data: { requestKey: randomUUID(), note: '真实隔离验收',
      lines: products[0].skus.map((sku: any) => ({ skuId: sku.id, quantity: sku.saleUnit === '瓶' ? 2 : 1,
        expectedCostRevision: sku.costRevision, expectedUnitVersionId: sku.currentUnitVersionId })) } })
    expect(created.status()).toBe(201)
    let order = (await created.json()).data
    expect(order.goodsAmountFen).toBe(48000)
    const path = `/api/v1/admin/stores/procurement/orders/${order.id}`
    await page.goto(`/stores/procurement/${order.id}`)
    const perform = async (label: string, fields: Record<string, string>, action: string) => {
      await page.getByRole('button', { name: label, exact: true }).click()
      const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: label, exact: true }) })
      for (const [name, value] of Object.entries(fields)) await dialog.getByLabel(name, { exact: true }).fill(value)
      await dialog.getByRole('button', { name: '确认提交', exact: true }).click()
      const response = page.waitForResponse(r => r.url().endsWith(`${path}/${action}`) && r.request().method() === 'POST')
      await page.getByRole('button', { name: '确认操作', exact: true }).click()
      expect((await response).status()).toBe(200)
      await expect(dialog).not.toBeVisible()
    }
    await perform('审单并确认运费', { '运费（元）': '10.00' }, 'review')
    const refresh = async () => (await (await member.get(`${base}/orders/${order.id}`)).json()).data
    order = await refresh()
    expect(order.payableFen).toBe(49000)
    const quote = await member.post(`${base}/orders/${order.id}/confirm-quote`, {
      data: { requestKey: randomUUID(), expectedRevision: order.revision } })
    expect(quote.status()).toBe(200)
    order = (await quote.json()).data
    const upload = await member.post(`${base}/proofs`, { multipart: { file: { name: 'payment.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC', 'base64') } } })
    expect(upload.status()).toBe(201)
    const proof = (await upload.json()).data
    expect((await anonymous.get(proof.previewUrl)).status()).toBeGreaterThanOrEqual(400)
    const report = await member.post(`${base}/orders/${order.id}/payment-proof`, {
      data: { requestKey: randomUUID(), expectedRevision: order.revision, assetId: proof.assetId, note: '隔离测试付款' } })
    expect(report.status()).toBe(200)
    await page.reload()
    await perform('确认实际到账', { '到账凭证编号': 'synthetic-cash-only', '处理备注': '实际足额核款' }, 'confirm-payment')
    await perform('填写物流并发货', { '物流公司': '测试物流', '物流单号': 'SYNTHETIC-001' }, 'ship')
    order = await refresh()
    expect(order.status).toBe('SHIPPED')
    const balances = async () => (await (await page.request.get(`/api/v1/admin/inventory/balances?warehouseId=${process.env.MALL_E2E_WAREHOUSE_ID}`)).json()).data.items
    const before = (await balances())[0]
    const receipt = { requestKey: randomUUID(), expectedRevision: order.revision, lineIds: order.lines.map((x: any) => x.id) }
    const receive = await member.post(`${base}/orders/${order.id}/receive`, { data: receipt })
    expect(receive.status()).toBe(200)
    expect((await receive.json()).data.status).toBe('RECEIVED')
    expect((await member.post(`${base}/orders/${order.id}/receive`, { data: receipt })).status()).toBe(200)
    const after = (await balances())[0]
    expect(after.onHandBaseUnits).toBe(before.onHandBaseUnits + 8)
    expect(after.reservedBaseUnits).toBe(3)
    await page.reload()
    await expect(page.getByText('已收货', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '填写物流并发货', exact: true })).toHaveCount(0)
  } finally { await member.dispose(); await anonymous.dispose() }
})
