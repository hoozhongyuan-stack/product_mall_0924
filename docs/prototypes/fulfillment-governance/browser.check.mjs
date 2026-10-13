import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/Users/hoo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = process.env.PROTOTYPE_URL || 'http://127.0.0.1:8872';
const review = fileURLToPath(new URL('.impeccable/review/', import.meta.url));
await mkdir(review, { recursive: true });
const cases = [];
const fresh = async role => {
  await page.goto(base);
  await page.locator('[data-role="platform"]').waitFor();
  if (role && role !== 'platform') await page.locator(`[data-role="${role}"]`).click();
};
const select = async id => page.locator('#scenario-select').selectOption(id);
const clickAction = async (action, id, fields = {}) => {
  await page.locator(`[data-action="${action}"][data-id="${id}"]`).click();
  for (const [name, value] of Object.entries(fields)) {
    const element = page.locator(`#action-dialog [name="${name}"]`);
    if (name === 'carrier') await element.selectOption(value);
    else await element.fill(String(value));
  }
  await page.locator('#confirm-action').click();
  await page.locator('#action-dialog').waitFor({ state: 'hidden' });
};
const expectStatus = async text => assert.match(await page.locator('.phone .mobile-status h2').innerText(), new RegExp(text));
const run = async (name, task) => {
  await task();
  cases.push({ name, passed: true });
  process.stdout.write(`PASS ${name}\n`);
};

try {
  await run('平台无前置仓配送操作，前置仓配送闭环和异常恢复', async () => {
    await fresh();
    await page.locator('[data-open="delivery"]').click();
    assert.equal(await page.locator('[data-action="PREPARE"]').count(), 0);
    await page.locator('[data-role="store"]').click();
    await select('delivery');
    const before = await page.locator('.annotation').innerText();
    await clickAction('PREPARE', 'delivery');
    await expectStatus('待配送');
    await clickAction('DISPATCH', 'delivery');
    await clickAction('FAIL_DELIVERY', 'delivery', { reason: '顾客暂时无法接收' });
    await expectStatus('配送异常');
    await clickAction('RETRY_DELIVERY', 'delivery');
    await clickAction('COMPLETE', 'delivery');
    await expectStatus('已完成');
    assert.equal(await page.locator('.annotation').innerText(), before);
    await page.locator('[data-to-role="customer"]').click();
    await expectStatus('已完成');
  });
  await run('顾客自提码，错误码拒绝，正确码交付', async () => {
    await fresh('customer'); await select('pickup');
    const code = (await page.locator('.voucher-code').innerText()).trim();
    await page.locator('[data-to-role="store"]').click();
    await page.locator('[data-action="PICKUP"]').click();
    await page.locator('[name="code"]').fill('000000');
    await page.locator('#verify-code').click();
    assert.match(await page.locator('#dialog-error').innerText(), /不匹配/);
    await page.locator('[name="code"]').fill(code);
    await page.locator('#verify-code').click();
    assert.match(await page.locator('#verification').innerText(), /凭证有效/);
    await page.locator('#confirm-action').click();
    await page.locator('#action-dialog').waitFor({ state: 'hidden' });
    await expectStatus('已完成');
  });
  await run('平台快递发货与顾客收货联动', async () => {
    await fresh();
    await clickAction('SHIP', 'express', { carrier: '顺丰速运', tracking: 'SF202610120001' });
    await page.locator('[data-role="customer"]').click();
    await select('express');
    assert.match(await page.locator('.phone').innerText(), /SF202610120001/);
    await clickAction('RECEIVE', 'express');
    await expectStatus('已完成');
    await page.locator('[data-role="store"]').click();
    assert.equal(await page.locator('.phone [data-open="express"]').count(), 0);
  });
  await run('平台核销后前置仓共享剩余次数', async () => {
    await fresh('customer'); await select('virtual');
    const code = (await page.locator('.voucher-code').innerText()).trim();
    await page.locator('[data-to-role="platform"]').click();
    await clickAction('REDEEM', 'virtual', { code, qty: 2 });
    await page.locator('[data-role="store"]').click(); await select('virtual');
    assert.match(await page.locator('.phone .available').innerText(), /2 次/);
    await clickAction('REDEEM', 'virtual', { code, qty: 2 });
    await expectStatus('已完成');
    await page.locator('[data-to-role="customer"]').click();
    assert.match(await page.locator('.phone .voucher-stats').innerText(), /5\n已核销/);
  });
  await run('权益售后冻结、退款作废与共享剩余量', async () => {
    await fresh('customer'); await select('virtual');
    await clickAction('APPLY_AFTER', 'virtual', { qty: 2, reason: '暂时不需要服务' });
    assert.match(await page.locator('.phone .available').innerText(), /2 次/);
    await page.locator('[data-to-role="platform"]').click();
    await clickAction('REFUND', 'virtual');
    await page.locator('[data-role="customer"]').click();
    assert.match(await page.locator('.phone .voucher-stats').innerText(), /2\n已作废/);
    assert.match(await page.locator('.phone .available').innerText(), /2 次/);
  });
  await run('实物部分冻结继续配送，剩余退款回补原仓', async () => {
    await fresh('customer'); await select('delivery');
    await clickAction('APPLY_AFTER', 'delivery', { qty: 1, reason: '其中一件不需要了' });
    await page.locator('[data-to-role="store"]').click();
    await clickAction('PREPARE', 'delivery'); await clickAction('DISPATCH', 'delivery'); await clickAction('COMPLETE', 'delivery');
    await expectStatus('售后处理中');
    assert.match(await page.locator('.phone').innerText(), /已交付1件/);
    await page.locator('[data-role="platform"]').click();
    await page.locator('[data-open="delivery"]').click();
    await clickAction('REFUND', 'delivery');
    assert.match(await page.locator('.action-box + .panel').innerText(), /21/);
    await page.locator('[data-to-role="customer"]').click(); await expectStatus('已完成');
  });
  await run('混合单服务售后不阻断礼盒发货，所有义务结束才完成', async () => {
    await fresh('customer'); await select('mixed');
    const code = (await page.locator('.voucher-code').innerText()).trim();
    await clickAction('APPLY_AFTER', 'mixed', { qty: 1, reason: '减少一次品鉴服务' });
    await page.locator('[data-to-role="platform"]').click();
    await clickAction('SHIP', 'mixed', { carrier: '中通快递', tracking: 'ZT202610120001' });
    await page.locator('[data-to-role="customer"]').click();
    await clickAction('RECEIVE', 'mixed'); await expectStatus('待核销');
    await page.locator('[data-to-role="platform"]').click();
    await clickAction('REFUND', 'mixed'); await clickAction('REDEEM', 'mixed', { code, qty: 2 });
    await page.locator('[data-to-role="customer"]').click(); await expectStatus('已完成');
  });
  await run('未付款不能履约，付款扣指定仓一次，取消只释放占用', async () => {
    await fresh('store'); await select('unpaid');
    assert.equal(await page.locator('.phone [data-action]').count(), 0);
    await page.locator('[data-to-role="customer"]').click();
    await clickAction('PAY', 'unpaid');
    assert.match(await page.locator('.annotation').innerText(), /18/);
    await expectStatus('待备货');
    await fresh('customer'); await select('unpaid'); await clickAction('CANCEL', 'unpaid');
    assert.match(await page.locator('.annotation').innerText(), /20/);
    await expectStatus('已取消');
  });
  await run('核销范围配置保留旧单，演示新单使用新范围', async () => {
    await fresh(); await page.locator('.subnav [data-admin-page="policy"]').click();
    await page.locator('[data-scope="STORE"]').click();
    await page.locator('[data-policy-store="sz"]').uncheck();
    await page.locator('[data-policy-store="ns"]').check();
    await page.locator('#save-policy').click(); await page.locator('#new-virtual').click();
    const definitions = await page.locator('.definition').allInnerTexts();
    assert.match(definitions[0], /南山/); assert.match(definitions[1], /深圳1号/); assert.match(definitions[2], /南山/);
    await page.locator('.definition [data-open]').click();
    assert.equal(await page.locator('[data-action="REDEEM"]').count(), 0);
    await page.locator('[data-role="store"]').click();
    await page.locator('#store-select').selectOption('ns');
    assert.match(await page.locator('.phone').innerText(), /新单/);
  });
  await run('虚拟授权仓筛选，搜索空态与恢复', async () => {
    await fresh();
    await page.locator('[name="owner"]').selectOption('sz');
    await page.locator('[name="search"]').fill('礼盒');
    await page.locator('#filters button').click();
    assert.match(await page.locator('tbody').innerText(), /礼盒/);
    await page.locator('[name="search"]').fill('不存在的商品'); await page.locator('#filters button').click();
    assert.match(await page.locator('.empty-state').innerText(), /暂无/);
    await page.locator('[data-view-state="normal"]').click(); assert.equal(await page.locator('tbody tr').count(), 6);
  });
  await run('手机异常状态与无权限时不显示私人订单', async () => {
    await fresh('store');
    for (const value of ['loading', 'error', 'empty', 'denied']) {
      await page.locator('#view-state').selectOption(value);
      assert.equal(await page.locator('.phone [data-view-state="normal"]').count(), 1);
      if (value === 'denied') {
        assert.equal(await page.locator('#scenario-select').count(), 0);
        assert.doesNotMatch(await page.locator('#app').innerText(), /精酿啤酒组合|销售账面余量/);
      }
      await page.locator('.phone [data-view-state="normal"]').click();
    }
  });
  await run('到期权益拒绝核销，平台专属和仓专属范围隔离', async () => {
    await fresh('customer'); await select('expired');
    assert.equal(await page.locator('.voucher-code').count(), 0);
    await page.locator('[data-to-role="platform"]').click(); assert.equal(await page.locator('[data-action="REDEEM"]').count(), 0);
    await page.locator('[data-role="customer"]').click(); await select('store-only');
    await page.locator('[data-to-role="platform"]').click(); assert.equal(await page.locator('[data-action="REDEEM"]').count(), 0);
    await page.locator('[data-role="store"]').click();
    assert.equal(await page.locator('#scenario-select option[value="platform-only"]').count(), 0);
  });

  await fresh();
  await page.screenshot({ path: `${review}desktop.png`, fullPage: true });
  await fresh('store');
  await page.screenshot({ path: `${review}store-desktop.png`, fullPage: true });
  await fresh('customer'); await select('virtual');
  await page.screenshot({ path: `${review}customer-desktop.png`, fullPage: true });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await fresh('customer'); await select('virtual');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await page.locator('.phone').evaluate(element => element.scrollWidth <= element.clientWidth));
    await page.screenshot({ path: `${review}${width === 390 ? 'mobile.png' : 'user-320.png'}`, fullPage: true });
    await fresh('store'); await select('pickup');
    await page.locator('[data-action="PICKUP"]').click();
    assert(await page.locator('#action-dialog').evaluate(element => element.getBoundingClientRect().width <= innerWidth));
    await page.locator('#action-dialog [data-close]').first().click();
    await fresh();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  cases.push({ name: '1440 / 390 / 320布局和弹窗宽度', passed: true });
  await run('独立HTML可通过本地文件直接打开并操作', async () => {
    await page.goto(new URL('订单履约治理交互稿.html', import.meta.url).href);
    await page.locator('[data-role="customer"]').click(); await select('unpaid');
    await clickAction('PAY', 'unpaid'); await expectStatus('待备货');
  });
  assert.deepEqual(errors, []);
  await writeFile(new URL('browser-results.json', import.meta.url), JSON.stringify({ passed: cases.length, failed: 0, errors, cases, screenshots: ['desktop.png', 'store-desktop.png', 'customer-desktop.png', 'mobile.png', 'user-320.png'] }, null, 2));
  process.stdout.write(`完成：${cases.length}项浏览器场景通过，无页面异常。\n`);
} finally { await browser.close(); }
