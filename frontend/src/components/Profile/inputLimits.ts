export const STATEMENT_MAX = 200
export const EXPLANATION_MAX = 4000
export const ITEMS_MAX = 50

export const characterCount = (value: string) => Array.from(value).length

export function textLimitMessage(
  value: string,
  limit: number,
  index: number,
  label = "说明",
) {
  const count = characterCount(value)
  return count > limit
    ? `${label}最多 ${limit} 字，第 ${index + 1} 条有 ${count} 字`
    : null
}
