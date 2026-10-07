import { expect, it, vi } from 'vitest'
import { ElMessageBox } from 'element-plus'
import { confirmAction } from '../../src/shared/confirm'

it('requires an affirmative choice, using a text-only centered confirmation', async () => {
  const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
  expect(await confirmAction('<b>放弃修改？</b>')).toBe(true)
  expect(confirm).toHaveBeenCalledWith('<b>放弃修改？</b>', '确认操作', expect.objectContaining({
    dangerouslyUseHTMLString: false, closeOnClickModal: false, distinguishCancelAndClose: true,
    confirmButtonText: '确认继续', cancelButtonText: '取消',
  }))
})
it.each(['cancel', 'close'])('treats %s as a refusal without throwing', async action => {
  vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce(action)
  expect(await confirmAction('离开？')).toBe(false)
})
it('does not reuse one choice to authorize two simultaneous actions and releases the lock afterwards', async () => {
  let finish!: (value: 'confirm') => void
  const confirm = vi.spyOn(ElMessageBox, 'confirm').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const first = confirmAction('离开编辑？')
  expect(await confirmAction('删除商品？')).toBe(false)
  expect(confirm).toHaveBeenCalledTimes(1)
  finish('confirm')
  expect(await first).toBe(true)
  confirm.mockRejectedValueOnce('cancel')
  expect(await confirmAction('删除商品？')).toBe(false)
  expect(confirm).toHaveBeenCalledTimes(2)
})

it('allows explicit action wording and fails closed on unexpected modal errors', async () => {
  const confirm = vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce(new Error('render failed'))
  expect(await confirmAction('放弃修改？', { title: '未保存修改', confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑', type: 'info' })).toBe(false)
  expect(confirm).toHaveBeenCalledWith(expect.any(String), '未保存修改', expect.objectContaining({ confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑', type: 'info' }))
})
