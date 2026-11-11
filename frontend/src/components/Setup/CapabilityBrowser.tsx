import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
  CapabilitiesService,
  type CapabilityPublic,
  type CapabilityStats,
} from "@/client"
import { record, text } from "@/components/Profile/fields"
import { labels } from "@/components/Profile/labels"
import { Input } from "@/components/ui/input"
import { SetupError } from "./SetupError"

const colors = ["bg-teal-300", "bg-teal-500", "bg-teal-700", "bg-teal-900"]

export function CapabilityBrowser({
  orgId,
  capabilities,
  showStats,
}: {
  orgId: string
  capabilities: CapabilityPublic[]
  showStats: boolean
}) {
  const [search, setSearch] = useState("")
  const stats = useQuery({
    queryKey: ["capability-stats", orgId],
    queryFn: async () =>
      (
        await CapabilitiesService.readCapabilityStats({
          path: { org_id: orgId },
        })
      ).data,
    enabled: showStats,
    retry: false,
  })
  const counts = new Map(stats.data?.data.map((s) => [s.capability_key, s]))
  const groups = new Map<string, CapabilityPublic[]>()
  const query = search.trim().toLocaleLowerCase()
  for (const capability of capabilities) {
    if (
      query &&
      !`${capability.name} ${capability.description ?? ""}`
        .toLocaleLowerCase()
        .includes(query)
    )
      continue
    const category = capability.category?.trim() || "未分类"
    const entries = groups.get(category) ?? []
    entries.push(capability)
    groups.set(category, entries)
  }
  return (
    <section aria-label="浏览能力词典" className="space-y-6">
      <Input
        type="search"
        aria-label="搜索能力"
        placeholder="按名称或说明搜索能力…"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {showStats && (
        <p className="text-sm text-muted-foreground">
          统计范围内在职员工最新发布的判断；未发布判断不计入人数。
        </p>
      )}
      {showStats && stats.isPending && <p role="status">正在加载人数统计…</p>}
      {showStats && <SetupError error={stats.error} />}
      {!groups.size && (
        <p role="status" className="py-8 text-center text-muted-foreground">
          {capabilities.length
            ? "没有匹配的能力，请调整搜索词。"
            : "暂无能力定义。"}
        </p>
      )}
      {Array.from(groups, ([category, entries]) => (
        <details key={category} open className="group space-y-4">
          <summary className="cursor-pointer border-b pb-3 text-lg font-semibold">
            {category}
            <span className="ml-3 text-sm font-normal text-muted-foreground">
              {entries.length} 项能力
            </span>
          </summary>
          <div className="grid gap-4 xl:grid-cols-2">
            {entries.map((capability) => (
              <CapabilityCard
                key={capability.id}
                capability={capability}
                stats={showStats ? counts.get(capability.key) : undefined}
              />
            ))}
          </div>
        </details>
      ))}
    </section>
  )
}

function CapabilityCard({
  capability,
  stats,
}: {
  capability: CapabilityPublic
  stats?: CapabilityStats
}) {
  const raw = capability.level_rubric?.levels
  const levels = Array.isArray(raw) ? raw.filter(record) : []
  const distribution = [
    ...levels.map((level) => ({
      key: text(level.key),
      label: text(level.label),
    })),
    ...Object.keys(stats?.levels ?? {})
      .filter((key) => key !== "_none" && !levels.some((l) => l.key === key))
      .map((key) => ({ key, label: "历史等级" })),
    { key: "_none", label: "未定等级" },
  ]
  return (
    <article
      aria-label={capability.name}
      className="min-w-0 space-y-5 rounded-xl border bg-card p-5"
    >
      <header className="space-y-2">
        <h3 className="text-lg font-semibold break-words">{capability.name}</h3>
        <p className="text-sm leading-relaxed text-muted-foreground break-words">
          {capability.description || "暂无说明"}
        </p>
      </header>
      <ol
        aria-label="等级说明"
        className="space-y-3 border-l-2 border-primary/20 pl-4"
      >
        {levels.map((level) => (
          <li key={text(level.key)} className="text-sm break-words">
            <span className="font-medium">{text(level.label)}</span>
            <p className="mt-1 leading-relaxed text-muted-foreground">
              {text(level.description)}
            </p>
          </li>
        ))}
      </ol>
      {stats && (
        <section
          aria-label="已发布判断统计"
          className="space-y-3 border-t pt-4"
        >
          <p className="text-sm">
            已发布人数{" "}
            <strong className="ml-2 text-xl tabular-nums">
              {stats.employee_count}
            </strong>
          </p>
          <div
            aria-hidden="true"
            className="flex h-2 overflow-hidden rounded-full bg-muted"
          >
            {distribution.map((level, index) => (
              <span
                key={level.key}
                className={
                  level.key === "_none"
                    ? "bg-slate-400"
                    : colors[index % colors.length]
                }
                style={{
                  width: `${stats.employee_count ? ((stats.levels[level.key] ?? 0) / stats.employee_count) * 100 : 0}%`,
                }}
              />
            ))}
          </div>
          <ul
            aria-label="等级分布"
            className="flex flex-wrap gap-x-4 gap-y-2 text-xs"
          >
            {distribution.map((level, index) => (
              <li key={level.key} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 rounded-full ${level.key === "_none" ? "bg-slate-400" : colors[index % colors.length]}`}
                />
                {level.label} {stats.levels[level.key] ?? 0} 人
              </li>
            ))}
          </ul>
          <ul
            aria-label="判断状态分布"
            className="flex flex-wrap gap-2 text-xs"
          >
            {Object.entries(labels.assessment_status).map(([status, label]) => (
              <li
                key={status}
                className={`rounded px-2 py-1 ${label.className}`}
              >
                {label.label} {stats.statuses[status] ?? 0} 人
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  )
}
