import { defineConfig } from '@playwright/test'

const integration = process.env.MALL_E2E_REAL === '1'
const baseURL = process.env.MALL_E2E_BASE_URL || 'http://127.0.0.1:19473'
const port = new URL(baseURL).port

export default defineConfig({
  testDir: './tests/browser',
  testMatch: integration ? '**/integration.spec.ts' : ['**/mocked.spec.ts', '**/inventory-pilot.spec.ts', '**/passwords.spec.ts'],
  outputDir: integration ? './test-results/integration' : './test-results/browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  // HTML reports record fill() arguments, including the one-run integration password.
  reporter: integration ? [['list']] : [['list'], ['html', { outputFolder: 'playwright-report/browser', open: 'never' }]],
  use: {
    baseURL,
    browserName: 'chromium',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    screenshot: 'on',
    // Full-stack traces may contain the ephemeral test password; do not retain them.
    trace: integration ? 'off' : 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: process.env.MALL_E2E_EXTERNAL_SERVER === '1' ? undefined : {
    command: `npm run dev -- --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
