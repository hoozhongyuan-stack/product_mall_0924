import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
const source = (name:string) => readFileSync(`${process.cwd()}/src/views/${name}.vue`, 'utf8')
describe('member marketing and system list contracts', () => {
  for (const name of ['AccountsView','GroupsView','MembersView','CouponsView','PointsExchangeView','AfterSalesView']) {
    it(`${name} uses common selection, read-only batch comparison, pager and modal operations`, () => {
      const text = source(name)
      for (const token of ['AdminListPager', 'AdminSelectionBar', 'AdminSelectedRowsDialog', 'admin-list-page', 'admin-list-table', 'el-dialog', 'resetFilters']) expect(text).toContain(token)
      expect(text).toContain('selectedIds.value = []')
    })
  }
  for (const name of ['MembersView','CouponsView','PointsExchangeView','AfterSalesView']) {
    it(`${name} requests the selected server page size`, () => {
      const text = source(name)
      expect(text).toMatch(/pageSize: ?String\(pageSize.value\)/)
    })
  }
  for (const name of ['MemberDetailView','CouponDetailView','PointsExchangeDetailView','AfterSaleDetailView']) {
    it(`${name} preserves deep links with an optional embedded target`, () => {
      const text = source(name)
      expect(text).toContain('embedded?: boolean')
      expect(text).toContain('defineExpose')
      expect(text).toContain('mayClose')
      expect(text).toMatch(/route.params./)
    })
  }
})
