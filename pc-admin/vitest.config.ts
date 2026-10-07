import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    include: ['tests/components/**/*.test.ts'],
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html'],
      include: ['src/shared/confirm.ts', 'src/views/catalog/SkuTable.vue', 'src/views/coupons/CouponRulePreview.vue', 'src/views/CodeDomainCheck.vue', 'src/views/UploadTaskResult.vue', 'src/App.vue', 'src/navigation.ts', 'src/views/DashboardView.vue', 'src/views/WechatIntegrationView.vue', 'src/views/integrations/wechat.ts', 'src/shared/persistent-operation.ts', 'src/shared/csv.mjs', 'src/views/pages/targets.ts', 'src/views/catalog/ProductMediaEditor.vue', 'src/views/catalog/ProductDescriptionEditor.vue', 'src/views/catalog/description-editor.ts', 'src/shared/asset-upload-queue.ts', 'src/shared/AssetUploadQueue.vue', 'src/shared/upload-navigation.ts', 'src/views/inventory/InventorySkuPicker.vue', 'src/views/inventory/InventorySkuSummary.vue', 'src/views/inventory/InventoryPoolBindingPanel.vue'],
      thresholds: { perFile: true, statements: 80, branches: 80, functions: 80, lines: 80 },
    },
  },
})
