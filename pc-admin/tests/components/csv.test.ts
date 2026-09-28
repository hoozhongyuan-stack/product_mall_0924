import { describe, expect, it } from 'vitest'
import { csvCell, csvTable } from '../../src/shared/csv.mjs'

describe('shared spreadsheet export encoding', () => {
  it.each(['=1+1', '+SUM(1)', '@SUM(1)', '-1', ' =1+1', '\uFEFF+SUM(1)', '\n@SUM(1)', '\u0000=1', ' \u001f-1', '\ttext', '\rtext', '\ntext'])('exports a literal cell for formula/control prefix %j', value => {
    expect(csvCell(value)).toBe(`"'${value}"`)
  })
  it('preserves ordinary text, numeric values and missing cells while escaping quotes', () => {
    expect(csvCell(' ordinary text')).toBe('" ordinary text"')
    expect(csvCell('报损"确认')).toBe('"报损""确认"')
    expect(csvCell(12.34)).toBe('"12.34"')
    expect(csvCell(null)).toBe('""')
    expect(csvCell(undefined)).toBe('""')
    expect(csvCell('line\nbreak')).toBe('"line\nbreak"')
  })
  it('serializes each row with a UTF-8 BOM and CRLF without exposing embedded formulas', () => {
    expect(csvTable([['单据', '说明'], ['IN-1', '=HYPERLINK("x")']])).toBe('\uFEFF"单据","说明"\r\n"IN-1","\'=HYPERLINK(""x"")"\r\n')
    expect(csvTable([])).toBe('\uFEFF\r\n')
  })
})
