import {afterEach,describe,expect,it,vi} from 'vitest'
import {createHash,webcrypto} from 'node:crypto'
import {canonicalBatchItems,profitBatchTarget} from '../../src/views/stores/profit-batch'
afterEach(()=>vi.unstubAllGlobals())
describe('batch profit confirmation binding',()=>{
 it('sorts and canonicalizes copies without mutating the selected revisions',()=>{const rows=[{skuId:'BBBB',expectedRevision:3},{skuId:'aaaa',expectedRevision:1}];expect(canonicalBatchItems(rows)).toEqual([{skuId:'aaaa',expectedRevision:1},{skuId:'bbbb',expectedRevision:3}]);expect(rows[0]?.skuId).toBe('BBBB')})
 it('binds price, ratio and all sorted SKU revisions to the backend canonical payload',async()=>{vi.stubGlobal('crypto',webcrypto);const body={items:[{skuId:'00000000-0000-0000-0000-000000000002',expectedRevision:0},{skuId:'00000000-0000-0000-0000-000000000001',expectedRevision:2}],purchaseCostFen:6000,platformShareBps:2000};const expected=createHash('sha256').update(JSON.stringify([6000,2000,[['00000000-0000-0000-0000-000000000001',2],['00000000-0000-0000-0000-000000000002',0]]])).digest('hex');expect(await profitBatchTarget(body)).toBe(expected);expect(await profitBatchTarget({...body,purchaseCostFen:6001})).not.toBe(expected);expect(await profitBatchTarget({...body,items:body.items.map(row=>({...row,expectedRevision:3}))})).not.toBe(expected)})
})
