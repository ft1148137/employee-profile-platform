import { useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { type ReactNode, useState } from "react"
import {
  CapabilitiesService,
  type CapabilityPublic,
  type CapabilityStats,
} from "@/client"
import {
  HelpTip,
  SectionCard,
  SideDrawer,
} from "@/components/Common/WorkbenchPrimitives"
import { record, text } from "@/components/Profile/fields"
import { labels } from "@/components/Profile/labels"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { SetupError } from "./SetupError"

const levelColors = ["bg-sky-300", "bg-sky-500", "bg-blue-600", "bg-blue-900"]
const categoryOf = (capability: CapabilityPublic) =>
  capability.category?.trim() || "未分类"

export function CapabilityBrowser({
  orgId,
  capabilities,
  showStats,
  history,
}: {
  orgId: string
  capabilities: CapabilityPublic[]
  showStats: boolean
  history: ReactNode
}) {
  const [search, setSearch] = useState("")
  const [category, setCategory] = useState<string | null>(null)
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
  const categories = new Map<string, number>()
  for (const capability of capabilities) {
    const name = categoryOf(capability)
    categories.set(name, (categories.get(name) ?? 0) + 1)
  }
  const selected = category && categories.has(category) ? category : null
  const query = search.trim().toLocaleLowerCase()
  const filtered = capabilities.filter(
    (capability) =>
      (selected === null || selected === categoryOf(capability)) &&
      `${capability.name} ${capability.description ?? ""} ${capability.key}`
        .toLocaleLowerCase()
        .includes(query),
  )

  return (
    <section
      aria-label="浏览能力词典"
      className="grid items-start gap-3 lg:grid-cols-[minmax(9rem,18%)_minmax(0,1fr)]"
    >
      <SectionCard className="space-y-2 p-2">
        <h2 className="px-2 pt-1 text-xs font-medium text-muted-foreground">
          能力分类
        </h2>
        <nav aria-label="能力分类" className="flex flex-wrap gap-1 lg:flex-col">
          {([[null, capabilities.length], ...categories] as const).map(
            ([name, count]) => (
              <Button
                key={name === null ? "all" : `category:${name}`}
                size="sm"
                variant="ghost"
                aria-pressed={selected === name}
                onClick={() => setCategory(name)}
                className="h-auto min-h-8 max-w-full justify-between whitespace-normal aria-pressed:bg-primary/10 aria-pressed:text-primary"
              >
                <span className="min-w-0 text-left wrap-anywhere">
                  {name ?? "全部"}
                </span>
                <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums">
                  {count}
                </span>
              </Button>
            ),
          )}
        </nav>
        {history && <div className="border-t px-2 pt-2">{history}</div>}
      </SectionCard>
      <SectionCard className="overflow-hidden p-0 [&_[data-slot=table-container]]:rounded-none [&_[data-slot=table-container]]:border-0 [&_[data-slot=table-container]]:border-t">
        <div className="flex items-center gap-2 p-2.5">
          <div className="relative w-full max-w-sm">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground"
            />
            <Input
              type="search"
              aria-label="搜索能力"
              placeholder="按名称、编号或说明搜索…"
              className="h-8 pl-8"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          {showStats && (
            <HelpTip label="统计口径" content="仅统计在职员工的已发布画像。" />
          )}
        </div>
        {showStats && stats.isPending && (
          <p role="status" className="px-3 pb-2 text-xs text-muted-foreground">
            正在加载人数统计…
          </p>
        )}
        {showStats && stats.isError && (
          <div className="px-3 pb-2">
            <SetupError error={stats.error} />
          </div>
        )}
        <Table
          aria-label="能力列表"
          className="table-fixed [&_td]:px-2 [&_th]:h-9 [&_th]:px-2 sm:[&_td]:px-3 sm:[&_th]:px-3"
        >
          <TableHeader>
            <TableRow>
              <TableHead>能力名称与定义</TableHead>
              {showStats && (
                <>
                  <TableHead className="w-20 whitespace-normal sm:w-32">
                    等级分布
                  </TableHead>
                  <TableHead className="w-14 text-right whitespace-normal sm:w-20">
                    覆盖人数
                  </TableHead>
                </>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((capability) => (
              <CapabilityRow
                key={capability.id}
                capability={capability}
                stats={counts.get(capability.key)}
                showStats={showStats}
              />
            ))}
          </TableBody>
        </Table>
        {!filtered.length && (
          <p
            role="status"
            className="p-5 text-center text-sm text-muted-foreground"
          >
            {capabilities.length
              ? "没有匹配的能力，请调整搜索词。"
              : "暂无能力定义。"}
          </p>
        )}
      </SectionCard>
    </section>
  )
}

function CapabilityRow({
  capability,
  stats,
  showStats,
}: {
  capability: CapabilityPublic
  stats?: CapabilityStats
  showStats: boolean
}) {
  const raw = capability.level_rubric?.levels
  const levels = Array.isArray(raw) ? raw.filter(record) : []
  const distribution = [
    ...levels.map((level) => ({
      key: text(level.key),
      label: text(level.label),
    })),
    ...Object.keys(stats?.levels ?? {})
      .filter(
        (key) => key !== "_none" && !levels.some((level) => level.key === key),
      )
      .map((key) => ({ key, label: `历史等级（${key}）` })),
    { key: "_none", label: "未定级" },
  ].map(({ key, label }, index) => ({
    key,
    label,
    count: stats?.levels[key] ?? 0,
    color:
      key === "_none"
        ? "bg-slate-400"
        : levelColors[index % levelColors.length],
  }))

  return (
    <TableRow className="relative h-11 cursor-pointer focus-within:bg-muted/50">
      <TableCell className="py-1">
        <SideDrawer
          title={<span className="wrap-anywhere">{capability.name}</span>}
          trigger={
            <button
              type="button"
              className="block w-full truncate rounded text-left text-sm font-medium outline-none after:absolute after:inset-0 focus-visible:underline"
            >
              {capability.name}
            </button>
          }
        >
          <div className="space-y-4 text-sm wrap-anywhere">
            <p className="text-xs text-muted-foreground">
              {capability.key} · {categoryOf(capability)}
              {stats && ` · 覆盖 ${stats.employee_count} 人`}
            </p>
            {capability.description && (
              <p className="text-muted-foreground">{capability.description}</p>
            )}
            <div className="space-y-2">
              <h3 className="font-semibold">等级标准</h3>
              <ol aria-label="等级说明" className="space-y-2">
                {levels.map((level, index) => (
                  <li
                    key={text(level.key)}
                    className="rounded-md border bg-muted/40 p-2.5"
                  >
                    <p className="flex flex-wrap items-center gap-2 font-medium">
                      <span
                        aria-hidden="true"
                        className={`size-2 rounded-full ${levelColors[index % levelColors.length]}`}
                      />
                      {text(level.label)}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      {text(level.description)}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
            {stats && (
              <section
                aria-label="已发布判断统计"
                className="space-y-3 border-t pt-3"
              >
                <h3 className="font-semibold">已发布判断统计</h3>
                <ul
                  aria-label="等级分布"
                  className="flex flex-wrap gap-x-3 gap-y-1.5 text-xs"
                >
                  {distribution.map(({ key, label, count, color }) => (
                    <li key={key} className="flex items-center gap-1.5">
                      <span
                        aria-hidden="true"
                        className={`size-2 rounded-full ${color}`}
                      />
                      {label} {count} 人
                    </li>
                  ))}
                </ul>
                <ul
                  aria-label="判断状态分布"
                  className="flex flex-wrap gap-1.5 text-xs"
                >
                  {Object.entries(labels.assessment_status).map(
                    ([status, label]) => (
                      <li
                        key={status}
                        className={`rounded px-2 py-1 ${label.className}`}
                      >
                        {label.label} {stats.statuses[status] ?? 0} 人
                      </li>
                    ),
                  )}
                </ul>
              </section>
            )}
          </div>
        </SideDrawer>
        {capability.description && (
          <p className="truncate text-xs text-muted-foreground">
            {capability.description}
          </p>
        )}
      </TableCell>
      {showStats && (
        <>
          <TableCell className="py-1">
            {stats ? (
              <div
                role="img"
                aria-label={distribution
                  .map(({ label, count }) => `${label} ${count} 人`)
                  .join("，")}
                className="flex h-2 overflow-hidden rounded-full bg-muted"
              >
                {distribution.map(({ key, count, color }) => (
                  <span
                    key={key}
                    className={color}
                    style={{ flexGrow: count }}
                  />
                ))}
              </div>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </TableCell>
          <TableCell className="py-1 text-right tabular-nums">
            {stats?.employee_count ?? "—"}
          </TableCell>
        </>
      )}
    </TableRow>
  )
}
