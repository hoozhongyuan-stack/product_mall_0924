import { ElMessageBox } from 'element-plus'

type ConfirmOptions = {
  title?: string
  confirmButtonText?: string
  cancelButtonText?: string
  type?: 'warning' | 'info' | 'success' | 'error'
}
let confirming = false

/** A single explicit choice authorizes only its initiating action; overlapping requests refuse. */
export async function confirmAction(message: string, options: ConfirmOptions = {}): Promise<boolean> {
  if (confirming) return false
  confirming = true
  try {
    await ElMessageBox.confirm(message, options.title ?? '确认操作', {
      confirmButtonText: options.confirmButtonText ?? '确认继续',
      cancelButtonText: options.cancelButtonText ?? '取消',
      type: options.type ?? 'warning',
      customClass: 'mall-confirm-dialog',
      dangerouslyUseHTMLString: false,
      closeOnClickModal: false,
      distinguishCancelAndClose: true,
    })
    return true
  } catch { return false }
  finally { confirming = false }
}
