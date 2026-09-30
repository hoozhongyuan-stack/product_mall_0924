/** The server is authoritative; this rule gives immediate form feedback. */
export const PASSWORD_MIN_LENGTH = 6
export const PASSWORD_POLICY_TEXT = `至少 ${PASSWORD_MIN_LENGTH} 个字符。`
export function passwordIssue(password: string): string {
  return password.length < PASSWORD_MIN_LENGTH ? `密码${PASSWORD_POLICY_TEXT}` : ''
}
