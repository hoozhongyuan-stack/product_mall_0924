import type { HotzoneArea } from './types'
export interface Point { x: number; y: number }
const clamp = (value: number, max = 1) => Math.max(0, Math.min(max, Number.isFinite(value) ? value : 0))
const rounded = (value: number) => Math.round(value * 10000) / 10000
const boundedSpan = (value: number, origin: number) => origin + value > 1 ? 1 - origin : value
export function drawArea(start: Point, end: Point) {
  const x = rounded(Math.min(clamp(start.x), clamp(end.x)))
  const y = rounded(Math.min(clamp(start.y), clamp(end.y)))
  return { x, y, width: boundedSpan(rounded(Math.abs(clamp(end.x) - clamp(start.x))), x), height: boundedSpan(rounded(Math.abs(clamp(end.y) - clamp(start.y))), y) }
}
export function moveArea(area: HotzoneArea, dx: number, dy: number): HotzoneArea {
  return { ...area, x: boundedSpan(rounded(clamp(area.x + dx, 1 - area.width)), area.width), y: boundedSpan(rounded(clamp(area.y + dy, 1 - area.height)), area.height) }
}
export function resizeArea(area: HotzoneArea, dx: number, dy: number): HotzoneArea {
  return { ...area, width: boundedSpan(rounded(Math.max(.01, clamp(area.width + dx, 1 - area.x))), area.x), height: boundedSpan(rounded(Math.max(.01, clamp(area.height + dy, 1 - area.y))), area.y) }
}
