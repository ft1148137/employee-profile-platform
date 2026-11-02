export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function text(value: unknown): string {
  return typeof value === "string" ? value : "—"
}

export function named(value: unknown): string {
  return record(value) ? text(value.name) : "—"
}

export function levelLabel(rubric: unknown, level: string): string {
  if (!record(rubric) || !Array.isArray(rubric.levels)) return level
  const entry = rubric.levels.find(
    (item: unknown) => record(item) && item.key === level,
  )
  return record(entry) && typeof entry.label === "string" ? entry.label : level
}

export function dateLabel(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString("zh-CN") : "—"
}
