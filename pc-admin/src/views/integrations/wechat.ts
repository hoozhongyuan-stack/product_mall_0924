import { ApiError } from '../../api'

export const integrationPath = '/integrations/wechat-mini-program'
export interface Check {
  revision: number
  status: 'SUCCESS' | 'FAILED' | 'UNAVAILABLE'
  code: 'OK' | 'ADMIN_CONFIRMATION_REQUIRED' | 'ADMIN_REJECTED' | 'INVALID_CREDENTIALS' | 'IP_NOT_ALLOWED' | 'PLATFORM_RATE_LIMITED' | 'PLATFORM_UNAVAILABLE' | 'PLATFORM_ERROR'
  checkedAt: string
}
export interface Integration {
  revision: number
  source: 'ENV' | 'MANAGED'
  appId: string
  secretConfigured: boolean
  keyAvailable: boolean
  identityBinding: { status: 'EMPTY' | 'BOUND' | 'MULTIPLE'; appId: string | null }
  paymentAppIdStatus: 'NOT_CONFIGURED' | 'MATCHED' | 'MISMATCHED'
  notificationsStatus: 'NOT_VERIFIED'
  lastCheck: Check | null
}
export const checkMessages: Record<Check['code'], string> = {
  OK: '应用凭据调用成功。真实登录与真机验收仍需单独完成。',
  ADMIN_CONFIRMATION_REQUIRED: '管理员须在微信平台确认后，再重新读取并校验。',
  ADMIN_REJECTED: '微信平台管理员已拒绝，请在微信平台核对后重新读取并校验。',
  INVALID_CREDENTIALS: '微信平台拒绝了应用凭据，请核对 AppID 与 AppSecret。',
  IP_NOT_ALLOWED: '服务器 IP 未获微信平台允许，请核对平台 IP 白名单。',
  PLATFORM_RATE_LIMITED: '微信平台暂时限流，请稍后重新读取并校验。',
  PLATFORM_UNAVAILABLE: '暂时无法连接或读取微信平台响应，请稍后重新读取并校验。',
  PLATFORM_ERROR: '微信平台返回异常，请核对平台状态后重新读取并校验。',
}
export const checkTitles: Record<Check['status'], string> = { SUCCESS: '凭据校验通过', FAILED: '凭据校验未通过', UNAVAILABLE: '暂时无法完成校验' }
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}

/** Select only public metadata; never spread a server payload into a view model. */
export function readIntegration(value: unknown): Integration {
  const row = object(value), identity = object(row.identityBinding)
  if (!integer(row.revision) || !['ENV', 'MANAGED'].includes(String(row.source)) || typeof row.appId !== 'string'
    || typeof row.secretConfigured !== 'boolean' || typeof row.keyAvailable !== 'boolean'
    || !['EMPTY', 'BOUND', 'MULTIPLE'].includes(String(identity.status))
    || !(identity.appId === null || typeof identity.appId === 'string')
    || identity.status === 'BOUND' && !identity.appId
    || !['NOT_CONFIGURED', 'MATCHED', 'MISMATCHED'].includes(String(row.paymentAppIdStatus))
    || row.notificationsStatus !== 'NOT_VERIFIED') throw new Error('配置响应不完整，请重新读取。')
  let lastCheck: Check | null = null
  if (row.lastCheck !== null) {
    const check = object(row.lastCheck)
    if (!integer(check.revision) || !Object.hasOwn(checkTitles, String(check.status)) || !Object.hasOwn(checkMessages, String(check.code))
      || (check.status === 'SUCCESS') !== (check.code === 'OK')
      || typeof check.checkedAt !== 'string' || !Number.isFinite(Date.parse(check.checkedAt))) throw new Error('校验响应不完整，请重新读取。')
    if (check.revision === row.revision) lastCheck = { revision: check.revision, status: check.status as Check['status'], code: check.code as Check['code'], checkedAt: check.checkedAt }
  }
  return { revision: row.revision, source: row.source as Integration['source'], appId: row.appId,
    secretConfigured: row.secretConfigured, keyAvailable: row.keyAvailable,
    identityBinding: { status: identity.status as Integration['identityBinding']['status'], appId: identity.appId as string | null },
    paymentAppIdStatus: row.paymentAppIdStatus as Integration['paymentAppIdStatus'], notificationsStatus: 'NOT_VERIFIED', lastCheck }
}

export function credentialIssue(config: Integration, appId: string, action: 'KEEP' | 'REPLACE', secret: string) {
  if (!/^wx[0-9a-fA-F]{16}$/.test(appId)) return 'AppID 应以 wx 开头，后接 16 位十六进制字符。'
  if (config.identityBinding.status === 'BOUND' && appId !== config.identityBinding.appId) return '已有会员身份绑定，只能使用已绑定的 AppID。'
  if (config.identityBinding.status === 'MULTIPLE' && appId !== config.appId) return '历史会员身份存在多个 AppID，只能保留当前 AppID。'
  if (action === 'KEEP' && (!config.secretConfigured || appId !== config.appId)) return '首次配置或修改 AppID 时，请选择替换密钥并输入 AppSecret。'
  if (action === 'REPLACE' && (!secret.trim() || Array.from(secret).length > 256 || /[\u0000-\u001f\u007f-\u009f]/u.test(secret))) return 'AppSecret 须为 1–256 个字符，不能全为空白或含控制字符。'
  return ''
}

export function safeFailure(reason: unknown) {
  const messages: Record<string, string> = {
    REVISION_CONFLICT: '配置已被修改，请重新读取最新修订。',
    APP_ID_LOCKED: 'AppID 受会员身份绑定限制，请重新读取后核对。',
    CREDENTIALS_NOT_CONFIGURED: '尚未配置完整凭据，请重新读取后补全。',
    CREDENTIALS_UNAVAILABLE: '凭据暂不可用，请联系部署管理员检查加密配置。',
    VALIDATION_FAILED: '配置未通过校验，请核对输入并重新读取。',
    RATE_LIMITED: '操作过于频繁，请稍后重新读取。',
  }
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录已失效，请重新登录。'
    if (reason.status === 403) return '权限或密码确认未通过，请重新读取后核对。'
    if (messages[reason.code]) return messages[reason.code]
  }
  return '无法确认服务端结果，请重新读取配置后核对。'
}
