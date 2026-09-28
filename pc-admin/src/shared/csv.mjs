/** Quote every cell and keep spreadsheet formulas as literal user-entered text. */
export function csvCell(value) {
  const text = String(value ?? '')
  const dangerous = /^[\s\u0000-\u001f\u007f\uFEFF]*[=+@\-]/.test(text) || /^[\t\r\n]/.test(text)
  const safe = dangerous ? `'${text}` : text
  return `"${safe.replaceAll('"', '""')}"`
}

export function csvTable(rows) {
  return '\uFEFF' + rows.map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}
