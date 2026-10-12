import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    // Bound worker count so DOM tests remain reliable alongside local services.
    maxWorkers: 4,
    include: ['tests/components/**/*.test.ts'],
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html'],
      include: ['src/api.ts', 'src/shared/navigation/useNavigationFeedback.ts', 'src/shared/navigation/AdminNavigationLink.vue', 'src/shared/navigation/AdminNavigationFeedback.vue', 'src/shared/media-library.ts', 'src/shared/list/AdminListPager.vue', 'src/shared/list/AdminCursorPager.vue', 'src/views/stores/ProductProfitView.vue', 'src/views/catalog/CategoryImageEditor.vue', 'src/views/FilingSettingsPanel.vue', 'src/shared/confirm.ts', 'src/views/catalog/SkuTable.vue', 'src/views/catalog/UnitConversionEditor.vue', 'src/views/coupons/CouponRulePreview.vue', 'src/views/CodeDomainCheck.vue', 'src/views/UploadTaskResult.vue', 'src/App.vue', 'src/navigation.ts', 'src/views/DashboardView.vue', 'src/views/WechatIntegrationView.vue', 'src/views/integrations/wechat.ts', 'src/shared/persistent-operation.ts', 'src/shared/csv.mjs', 'src/views/pages/targets.ts', 'src/views/catalog/ProductMediaEditor.vue', 'src/views/catalog/ProductDescriptionEditor.vue', 'src/views/catalog/description-editor.ts', 'src/shared/asset-upload-queue.ts', 'src/shared/AssetUploadQueue.vue', 'src/shared/upload-navigation.ts', 'src/views/inventory/InventorySkuPicker.vue', 'src/views/inventory/InventorySkuSummary.vue', 'src/views/pages/use-release-report.ts', 'src/views/pages/CouponFields.vue', 'src/views/pages/coupon-preview.ts', 'src/views/pages/ReleaseReportPanel.vue', 'src/views/pages/release-report.ts', 'src/views/pages/PageReusePanel.vue', 'src/views/pages/PageMetadataEditor.vue', 'src/views/pages/MosaicFields.vue', 'src/views/pages/page-reuse.ts', 'src/views/pages/editor-history.ts', 'src/views/pages/hotzone-geometry.ts', 'src/views/pages/product-preview.ts', 'src/views/pages/editor-runtime.ts', 'src/views/pages/types.ts', 'src/views/pages/EditorActions.vue', 'src/views/pages/HotzoneCanvas.vue', 'src/views/pages/PageCopyPanel.vue', 'src/views/pages/PageContentFields.vue', 'src/views/pages/HomePagePreview.vue', 'src/views/pages/PageComponentEditor.vue'],
      thresholds: { perFile: true, statements: 80, branches: 80, functions: 80, lines: 80 },
    },
  },
})
