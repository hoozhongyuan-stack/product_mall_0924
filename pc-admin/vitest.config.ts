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
      include: ['src/App.vue', 'src/navigation.ts', 'src/views/DashboardView.vue', 'src/shared/persistent-operation.ts', 'src/shared/csv.mjs', 'src/views/pages/targets.ts', 'src/views/catalog/ProductMediaEditor.vue'],
      thresholds: { perFile: true, statements: 80, branches: 80, functions: 80, lines: 80 },
    },
  },
})
