import { ElMessageBox } from 'element-plus'

export async function confirmLeavingUploads(): Promise<boolean> {
  try {
    await ElMessageBox.confirm('离开后，等待中的文件将不再上传。当前请求可能仍在服务器完成，已成功上传的素材会保留。', '还有未完成的上传', {
      confirmButtonText: '离开', cancelButtonText: '继续上传', type: 'warning',
    })
    return true
  } catch { return false }
}
