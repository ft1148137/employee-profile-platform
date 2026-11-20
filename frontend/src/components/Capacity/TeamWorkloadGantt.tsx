import type { ComponentProps, ReactNode } from "react"
import type { WeekLoad, WorkloadPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useIsMobile } from "@/hooks/useMobile"
import { cn } from "@/lib/utils"
import {
  addWeeks,
  loadBandLabel,
  loadColors,
  UtilizationBadge,
  unavailableStripes,
  weekBand,
  weekLabel,
  weekLoadLabel,
} from "./shared"

const timelineWidth = "min-w-[52rem]"
const segmentKinds = {
  committed: {
    label: "已分配任务",
    color:
      "border-blue-300 bg-blue-100 text-blue-950 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-100",
  },
  borrow: {
    label: "跨部门借调",
    color:
      "border-violet-300 bg-violet-100 text-violet-950 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-100",
  },
  hold: {
    label: "方案暂留",
    color:
      "border-dashed border-amber-600 bg-amber-100 text-amber-950 dark:bg-amber-950 dark:text-amber-100",
  },
  unavailable: { label: "不可用", color: unavailableStripes },
}
const baseline = "bg-teal-500 dark:bg-teal-400"

function segmentsFor(row: WorkloadPublic) {
  return [
    ...(row.assignments ?? []).map((item) => ({
      ...item,
      end_date: item.due_date,
      label: `${item.kind === "borrow" ? "借调 · " : ""}${item.title} · ${item.allocation_percent}%`,
      note: item.legacy_start ? "开始日期未确认" : null,
    })),
    ...(row.holds ?? []).map((item, index) => ({
      ...item,
      id: `hold-${item.proposal_id}-${index}`,
      kind: "hold" as const,
      label: `暂留 · ${item.title} · ${item.allocation_percent}%`,
      note: `待确认，到期：${new Date(item.expires_at).toLocaleString("zh-CN")}`,
    })),
    ...(row.unavailabilities ?? []).map((item) => ({
      ...item,
      kind: "unavailable" as const,
      label: "不可用日期",
      note: null,
    })),
  ]
}

type Segment = ReturnType<typeof segmentsFor>[number]
type DateRange = Pick<Segment, "start_date" | "end_date">
export type WorkloadSelection = {
  row: WorkloadPublic
  range: DateRange
  title: string
  week: string
}

function weekRange(start: string): DateRange {
  const sunday = new Date(start)
  sunday.setUTCDate(sunday.getUTCDate() + 6)
  return { start_date: start, end_date: sunday.toISOString().slice(0, 10) }
}

function overlaps(a: DateRange, b: DateRange) {
  return a.start_date <= b.end_date && b.start_date <= a.end_date
}

function otherWork(week: WeekLoad) {
  return `其他工作占比 ${week.other_percent === null ? "未确认" : `${week.other_percent}%`}`
}

export function WorkloadLegend() {
  const entries = [
    ...Object.entries(loadColors).map(([band, color]) => [
      loadBandLabel(band as WorkloadPublic["band"]),
      color.bar,
    ]),
    ...Object.values(segmentKinds).map(({ label, color }) => [label, color]),
    ["其他工作", `border-transparent ${baseline}`],
  ]
  return (
    <ul
      aria-label="甘特图图例"
      className="flex flex-wrap gap-x-3 gap-y-1.5 text-xs"
    >
      {entries.map(([label, color]) => (
        <li key={label} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={cn("h-2.5 w-3.5 rounded-xs border", color)}
          />
          {label === "超负荷" ? "超负荷（>100% 或超可用）" : label}
        </li>
      ))}
    </ul>
  )
}

function CurrentWeekLine({
  weeks,
  current,
}: {
  weeks: WeekLoad[]
  current: string
}) {
  const index = weeks.findIndex((week) => week.week_start === current)
  return index < 0 ? null : (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute inset-y-0 z-10 border-l-2 border-primary"
      style={{ left: `${index * 12.5}%` }}
    />
  )
}

function WeekHeader({
  weeks,
  current,
}: {
  weeks: WeekLoad[]
  current: string
}) {
  return (
    <div className="grid grid-cols-8 border-b bg-muted/60">
      {weeks.map(({ week_start }) => (
        <div
          key={week_start}
          className={cn(
            "flex items-center justify-between gap-1 border-r px-2.5 py-2 text-xs font-medium last:border-r-0",
            week_start === current && "bg-primary/10 text-primary",
          )}
        >
          <time dateTime={week_start}>{weekLabel(week_start)}</time>
          {week_start === current && (
            <span className="font-semibold">本周</span>
          )}
        </div>
      ))}
    </div>
  )
}

function TimelineButton({
  tooltip,
  ...props
}: ComponentProps<"button"> & { tooltip: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-haspopup="dialog" {...props} />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs break-words">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  )
}

function Timeline({
  row,
  activeWeek,
  onSelect,
}: {
  row: WorkloadPublic
  activeWeek?: string
  onSelect: (selection: WorkloadSelection, opener: HTMLButtonElement) => void
}) {
  const weeks = row.weeks
  const first = weeks[0].week_start
  const end = addWeeks(first, weeks.length)
  const dayIndex = (date: string) =>
    (Date.parse(date) - Date.parse(first)) / 86_400_000
  const segments = segmentsFor(row)

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-8">
        {weeks.map((week) => {
          const range = weekRange(week.week_start)
          const label = weekLoadLabel(week)
          const band = weekBand(week)
          const state = loadBandLabel(band)
          const unavailable =
            week.capacity_percent < 100 ||
            (row.unavailabilities ?? []).some((item) => overlaps(range, item))
          const allocation = week.platform_percent - (week.hold_percent ?? 0)
          return (
            <TimelineButton
              key={week.week_start}
              aria-pressed={week.week_start === activeWeek}
              tooltip={`${otherWork(week)} · 分配 ${allocation}% · 预占 ${week.hold_percent ?? 0}%`}
              aria-label={`${weekLabel(week.week_start)}，${label}，${state}${unavailable ? "，有不可用日期" : ""}，查看负荷明细`}
              className={cn(
                "m-0.5 min-w-0 cursor-pointer rounded-md border px-2 py-1.5 text-left text-xs outline-offset-2 hover:border-primary focus-visible:outline-2 focus-visible:outline-primary",
                loadColors[band].surface,
                unavailable && unavailableStripes,
                week.week_start === activeWeek &&
                  "ring-2 ring-primary ring-inset",
              )}
              onClick={(event) =>
                onSelect(
                  {
                    row,
                    range,
                    title: weekLabel(week.week_start),
                    week: week.week_start,
                  },
                  event.currentTarget,
                )
              }
            >
              <span
                className={cn(
                  "block font-semibold tabular-nums",
                  band === "overloaded" && "text-red-700 dark:text-red-300",
                )}
              >
                {label}
              </span>
              <span className="block text-muted-foreground">
                分配 {allocation}%
              </span>
              {week.holds?.map((hold, index) => (
                <span
                  key={index}
                  className="block text-amber-800 dark:text-amber-300"
                >
                  预占 {hold.allocation_percent}%，
                  {new Date(hold.expires_at).toLocaleDateString("zh-CN", {
                    month: "long",
                    day: "numeric",
                  })}
                  前待对方确认
                </span>
              ))}
              {week.capacity_percent < 100 && (
                <span className="block">可用 {week.capacity_percent}%</span>
              )}
              {row.correction_pending[week.week_start] && (
                <span className="block text-amber-700 dark:text-amber-300">
                  调整审批中
                </span>
              )}
              {week.legacy_start && (
                <span className="block">开始日期未确认</span>
              )}
              {week.other_percent !== null && (
                <span
                  aria-hidden="true"
                  className="mt-1 block h-1 rounded-full bg-muted"
                >
                  <span
                    className={cn("block h-full rounded-full", baseline)}
                    style={{
                      width: `${Math.min(week.other_percent, 100)}%`,
                    }}
                  />
                </span>
              )}
            </TimelineButton>
          )
        })}
      </div>
      {!!segments.length && (
        <div className="grid grid-cols-[repeat(56,minmax(0,1fr))] gap-y-1 bg-[linear-gradient(to_right,transparent_calc(100%_-_1px),var(--border)_0)] bg-size-[12.5%_100%] pb-1.5">
          {segments.map((segment, index) => {
            const startDay = Math.max(0, dayIndex(segment.start_date))
            const endDay = Math.min(56, dayIndex(segment.end_date) + 1)
            if (endDay <= startDay) return null
            const title = segment.label
            return (
              <TimelineButton
                key={segment.id}
                tooltip={
                  <>
                    {title} · {segment.start_date} 至 {segment.end_date}
                    {segment.note && <p>{segment.note}</p>}
                  </>
                }
                aria-label={`${segmentKinds[segment.kind].label}：${title}，${segment.start_date} 至 ${segment.end_date}`}
                className={cn(
                  "mx-px flex h-7 min-w-0 cursor-pointer items-center gap-0.5 rounded border px-1 text-left text-xs font-medium hover:ring-1 hover:ring-primary focus-visible:outline-2 focus-visible:outline-primary",
                  segmentKinds[segment.kind].color,
                )}
                style={{
                  gridColumn: `${startDay + 1} / ${endDay + 1}`,
                  gridRow: index + 1,
                }}
                onClick={(event) =>
                  onSelect(
                    {
                      row,
                      range: segment,
                      title,
                      week: weeks[Math.floor(startDay / 7)].week_start,
                    },
                    event.currentTarget,
                  )
                }
              >
                {segment.start_date < first && (
                  <span aria-hidden="true">←</span>
                )}
                <span className="truncate">{title}</span>
                {segment.end_date >= end && (
                  <span aria-hidden="true" className="ml-auto">
                    →
                  </span>
                )}
              </TimelineButton>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function TeamWorkloadGantt({
  rows,
  current,
  actions,
  activeEmployee,
  activeWeek,
  onSelect,
}: {
  rows: WorkloadPublic[]
  current: string
  actions: (row: WorkloadPublic) => ReactNode
  activeEmployee?: string
  activeWeek?: string
  onSelect: (selection: WorkloadSelection, opener: HTMLButtonElement) => void
}) {
  const mobile = useIsMobile()
  const groups = new Map<string | null, WorkloadPublic[]>()
  for (const row of rows) {
    const group = groups.get(row.department_id)
    if (group) group.push(row)
    else groups.set(row.department_id, [row])
  }
  if (!rows.length)
    return (
      <p className="rounded-xl border p-4 text-sm text-muted-foreground">
        范围内暂无员工。
      </p>
    )

  const identity = (row: WorkloadPublic) => (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-1.5">
        <p className="min-w-0 break-words text-sm font-semibold">
          {row.employee_name}
        </p>
        <UtilizationBadge band={row.band} percent={row.confirmed_percent} />
      </div>
      {mobile && (
        <p className="text-xs text-muted-foreground">
          {row.department_name ?? "未分配部门"}
        </p>
      )}
      {actions(row)}
    </div>
  )

  return !mobile ? (
    <div className="overflow-x-auto border-surface-border border-b bg-surface">
      <table className="w-full text-left">
        <caption className="sr-only">团队负荷，按部门、姓名排序</caption>
        <thead>
          <tr>
            <th
              scope="col"
              className="sticky left-0 z-20 w-44 min-w-44 border-r border-b bg-muted px-3 text-sm"
            >
              成员与本周利用率
            </th>
            <th scope="col" className={cn(timelineWidth, "relative p-0")}>
              <CurrentWeekLine weeks={rows[0].weeks} current={current} />
              <WeekHeader weeks={rows[0].weeks} current={current} />
            </th>
          </tr>
        </thead>
        {Array.from(groups, ([id, group]) => (
          <tbody key={id ?? ""}>
            <tr className="bg-muted/70">
              <th
                scope="rowgroup"
                colSpan={2}
                className="p-0 text-xs font-semibold"
              >
                <span className="sticky left-0 inline-block px-3 py-2">
                  {group[0].department_name ?? "未分配部门"}
                </span>
              </th>
            </tr>
            {group.map((row) => (
              <tr key={row.employee_id} className="border-t align-top">
                <th
                  scope="row"
                  className="sticky left-0 z-20 w-44 border-r bg-surface p-2.5 font-normal"
                >
                  {identity(row)}
                </th>
                <td className="relative p-0">
                  <CurrentWeekLine weeks={row.weeks} current={current} />
                  <Timeline
                    row={row}
                    activeWeek={
                      row.employee_id === activeEmployee
                        ? activeWeek
                        : undefined
                    }
                    onSelect={onSelect}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  ) : (
    <div className="space-y-4 p-3">
      {Array.from(groups, ([id, group]) => (
        <section
          key={id ?? ""}
          aria-label={group[0].department_name ?? "未分配部门"}
          className="space-y-2"
        >
          <h2 className="text-sm font-semibold">
            {group[0].department_name ?? "未分配部门"}
          </h2>
          {group.map((row) => (
            <article
              key={row.employee_id}
              aria-label={row.employee_name}
              className="overflow-hidden rounded-lg border bg-surface"
            >
              <div className="space-y-2 p-3">
                {identity(row)}
                <p className="text-xs text-muted-foreground">
                  左右滑动查看 8 周排期
                </p>
              </div>
              <div className="relative border-t">
                <section
                  aria-label={`${row.employee_name}的 8 周排期`}
                  className="overflow-x-auto"
                >
                  <div className={cn(timelineWidth, "relative")}>
                    <CurrentWeekLine weeks={row.weeks} current={current} />
                    <WeekHeader weeks={row.weeks} current={current} />
                    <Timeline
                      row={row}
                      activeWeek={
                        row.employee_id === activeEmployee
                          ? activeWeek
                          : undefined
                      }
                      onSelect={onSelect}
                    />
                  </div>
                </section>
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 right-0 z-10 w-5 bg-linear-to-l from-background/85 to-transparent"
                />
              </div>
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}

export function WorkloadDetails({
  selection: { row, range, title },
}: {
  selection: WorkloadSelection
}) {
  const allSegments = segmentsFor(row)
  const segments = allSegments.filter((segment) => overlaps(segment, range))

  return (
    <>
      <p className="break-words text-sm text-muted-foreground">
        {title} · {range.start_date} 至 {range.end_date}
      </p>
      <p className="text-xs text-muted-foreground">
        Jira 参考值：
        {row.jira_task_count === null
          ? "暂无（未关联 Jira）"
          : `${row.jira_task_count} 件未完成`}
      </p>
      <p className="text-xs text-muted-foreground">
        上次确认：
        {row.last_confirmed
          ? `${row.last_confirmed.percent}%（${weekLabel(row.last_confirmed.week_start)}）`
          : "暂无确认"}
      </p>

      <ul className="space-y-1 rounded-lg bg-muted/50 p-3 text-sm">
        {row.weeks
          .filter((week) => overlaps(weekRange(week.week_start), range))
          .map((week) => {
            const dates = weekRange(week.week_start)
            let visibleAssigned = 0
            let visibleHeld = 0
            for (const segment of allSegments) {
              if (!overlaps(dates, segment)) continue
              if (segment.kind === "hold") {
                visibleHeld += segment.allocation_percent
              } else if (segment.kind !== "unavailable") {
                visibleAssigned += segment.allocation_percent
              }
            }
            const held = week.hold_percent ?? 0
            const hidden =
              Math.max(0, week.platform_percent - held - visibleAssigned) +
              Math.max(0, held - visibleHeld)
            return (
              <li key={week.week_start}>
                {weekLabel(week.week_start)} · {otherWork(week)} ·{" "}
                {weekLoadLabel(week)}
                {hidden > 0 && (
                  <p className="text-xs text-muted-foreground">
                    另有未展开或待确认负荷 {hidden}%
                  </p>
                )}
              </li>
            )
          })}
      </ul>
      <div className="space-y-3 text-sm">
        {Object.entries(segmentKinds).map(([kind, { label }]) => {
          const matching = segments.filter((segment) => segment.kind === kind)
          if (!matching.length) return null
          return (
            <section key={kind} aria-label={label} className="space-y-1.5">
              <Badge variant="outline">{label}</Badge>
              <ul className="space-y-1.5">
                {matching.map((segment) => (
                  <li key={segment.id} className="break-words border-l-2 pl-2">
                    <p className="font-medium">{segment.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {segment.start_date} 至 {segment.end_date}
                      {segment.note && ` · ${segment.note}`}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>
    </>
  )
}
