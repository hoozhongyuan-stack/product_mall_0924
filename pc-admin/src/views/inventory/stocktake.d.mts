export function parseCount(value: string): number | null
export function buildStocktakeSubmission(
  items: { skuId: string; skuCode?: string; bookAtStartBaseUnits: number }[],
  values: Record<string, { count: string; reason: string }>,
): { skuId: string; countedBaseUnits: number; reason: string }[]
