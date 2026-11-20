import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ChevronDown } from "lucide-react"
import { useState } from "react"
import type { DemandPublic, PendingReviews } from "@/client"
import { Approvals } from "@/components/Capacity/Approvals"
import { BorrowRequestCard } from "@/components/Capacity/BorrowRequests"
import { WorkspacePage } from "@/components/Common/WorkbenchPrimitives"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import {
  requestClientName,
  viaLabels,
} from "@/components/Profile/RequestTimeline"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import {
  PENDING_DEMAND_PAGE_SIZE,
  pendingDemandPageOptions,
  usePendingApprovals,
} from "@/hooks/usePendingApprovals"
import { cn } from "@/lib/utils"

const filters = {
  all: "全部",
  profile: "画像变更",
  workload: "负荷与延期",
  demand: "协作需求",
  borrow: "借调确认",
}

type ApprovalType = keyof typeof filters

const filterStyles = {
  all: "aria-[current=page]:border-primary/40 aria-[current=page]:bg-primary/10 aria-[current=page]:text-primary",
  profile:
    "aria-[current=page]:border-primary/40 aria-[current=page]:bg-primary/10 aria-[current=page]:text-primary",
  workload:
    "aria-[current=page]:border-muted-foreground/40 aria-[current=page]:bg-muted",
  demand:
    "aria-[current=page]:border-amber-500/40 aria-[current=page]:bg-amber-500/10 aria-[current=page]:text-amber-800 dark:aria-[current=page]:text-amber-300",
  borrow:
    "aria-[current=page]:border-violet-500/40 aria-[current=page]:bg-violet-500/10 aria-[current=page]:text-violet-700 dark:aria-[current=page]:text-violet-300",
}

const rowClass =
  "flex min-h-11 min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-sm"
const badgeClass =
  "rounded-full border border-surface-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground"

export const Route = createFileRoute("/_layout/reviews/")({
  component: ReviewsPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { type?: ApprovalType } => ({
    type:
      typeof search.type === "string" &&
      Object.keys(filters).includes(search.type)
        ? (search.type as ApprovalType)
        : undefined,
  }),
  head: () => ({ meta: [{ title: "待审批 - 员工画像平台" }] }),
})

function ReviewsPage() {
  const { type = "all" } = Route.useSearch()
  const { org, employeeId } = useOrg()
  const pending = usePendingApprovals()
  if (!org) return <OrgState />
  const { capacity, profile, counts, totalCount } = pending
  const sections = [
    {
      type: "profile",
      queries: [profile],
      count: counts.profile,
      content: <ProfileReviews data={profile.data} />,
    },
    {
      type: "workload",
      queries: pending.workloadQueries,
      count: counts.workload,
      content: (
        <Approvals
          orgId={org.id}
          pendingWorkloads={pending.pendingWorkloads}
          pendingExtensions={pending.pendingExtensions}
        />
      ),
    },
    {
      type: "demand",
      queries: pending.demandQueries,
      count: counts.demand,
      content: (
        <DemandReviews
          key={org.id}
          orgId={org.id}
          employeeId={employeeId}
          firstPage={pending.demands}
          count={counts.demand}
        />
      ),
    },
    {
      type: "borrow",
      queries: pending.borrowQueries,
      count: counts.borrow,
      content: pending.borrows.map(({ request, mine }) => (
        <details
          key={request.id}
          className="group border-surface-border border-b last:border-0"
        >
          <summary
            className={cn(
              rowClass,
              "cursor-pointer list-none hover:bg-muted/60 [&::-webkit-details-marker]:hidden",
            )}
          >
            <span className={badgeClass}>借调确认</span>
            <span className="min-w-0 grow wrap-anywhere font-medium">
              {request.capability_name} · {request.borrower_department} ·{" "}
              {request.description}
            </span>
            <span className="text-xs text-muted-foreground">
              {mine ? "待本人确认" : "待确认人选"}
            </span>
            <span className="ml-auto inline-flex items-center gap-1 text-primary">
              处理{" "}
              <ChevronDown
                aria-hidden="true"
                className="size-4 group-open:rotate-180"
              />
            </span>
          </summary>
          <div className="p-3">
            <BorrowRequestCard orgId={org.id} request={request} mine={mine} />
          </div>
        </details>
      )),
    },
  ] as const
  return (
    <WorkspacePage className="space-y-3">
      <header>
        <h1 className="sr-only">待审批</h1>
        <nav aria-label="待审批类型" className="flex flex-wrap gap-2">
          {(Object.keys(filters) as ApprovalType[]).map((filter) => (
            <Link
              key={filter}
              to="/reviews"
              search={{ type: filter === "all" ? undefined : filter }}
              activeOptions={{
                exact: true,
                includeSearch: true,
                explicitUndefined: true,
              }}
              aria-current={type === filter ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-full border border-surface-border bg-surface px-3 py-1 text-sm hover:bg-muted aria-[current=page]:font-medium",
                filterStyles[filter],
              )}
            >
              {filters[filter]}
              <span className="tabular-nums">
                {filter === "all" ? totalCount : counts[filter]}
              </span>
            </Link>
          ))}
        </nav>
      </header>
      {type !== "profile" && capacity.isLoading && <ProfileLoading />}
      {type !== "profile" && capacity.isError && (
        <ProfileError retry={() => capacity.refetch()} />
      )}
      {type === "all" &&
        !totalCount &&
        capacity.isSuccess &&
        sections.every(({ queries }) =>
          queries.every((query) => query.isSuccess),
        ) && (
          <p role="status" className="text-muted-foreground">
            暂无待审批事项
          </p>
        )}
      <div className="divide-y divide-surface-border rounded-lg border border-surface-border bg-surface empty:hidden">
        {sections
          .filter(
            ({ type: sectionType }) =>
              (type === "all" || sectionType === type) &&
              (sectionType === "profile" || capacity.isSuccess),
          )
          .map((section) => {
            const loading = section.queries.some((query) => query.isPending)
            const failed = section.queries.some((query) => query.isError)
            if (type === "all" && !section.count && !loading && !failed)
              return null
            return (
              <section
                key={section.type}
                aria-label={filters[section.type]}
                className="min-w-0"
              >
                <h2 className="sr-only">{filters[section.type]}</h2>
                {loading ? (
                  <ProfileLoading />
                ) : failed ? (
                  <ProfileError
                    retry={() =>
                      section.queries.forEach((query) => void query.refetch())
                    }
                  />
                ) : section.type === "profile" ||
                  section.count ||
                  (section.type === "workload" && section.queries.length) ? (
                  section.content
                ) : (
                  <p
                    role="status"
                    className="p-3 text-sm text-muted-foreground"
                  >
                    暂无待审批事项
                  </p>
                )}
              </section>
            )
          })}
      </div>
    </WorkspacePage>
  )
}

function DemandReviews({
  orgId,
  employeeId,
  firstPage,
  count,
}: {
  orgId: string
  employeeId?: string | null
  firstPage: DemandPublic[]
  count: number
}) {
  const [skip, setSkip] = useState(0)
  const laterPage = useQuery({
    ...pendingDemandPageOptions(orgId, skip),
    enabled: skip > 0,
  })
  const rows = skip
    ? (laterPage.data?.data.filter((row) => row.can_reply === true) ?? [])
    : firstPage
  return (
    <>
      {(count > PENDING_DEMAND_PAGE_SIZE || skip > 0) && (
        <nav
          aria-label="协作需求分页"
          className="flex items-center justify-between gap-2 border-surface-border border-b p-2"
        >
          <Button
            variant="outline"
            disabled={skip === 0}
            onClick={() => setSkip(skip - PENDING_DEMAND_PAGE_SIZE)}
          >
            上一页
          </Button>
          <span role="status" className="text-sm">
            第 {skip / PENDING_DEMAND_PAGE_SIZE + 1} 页 · 共 {count} 条
          </span>
          <Button
            variant="outline"
            disabled={
              skip + PENDING_DEMAND_PAGE_SIZE >= count ||
              (skip > 0 && !laterPage.isSuccess)
            }
            onClick={() => setSkip(skip + PENDING_DEMAND_PAGE_SIZE)}
          >
            下一页
          </Button>
        </nav>
      )}
      {skip > 0 && laterPage.isPending ? (
        <ProfileLoading />
      ) : skip > 0 && laterPage.isError ? (
        <ProfileError retry={() => laterPage.refetch()} />
      ) : rows.length ? (
        rows.map((row) => (
          <article
            key={row.id}
            className={cn(
              rowClass,
              "border-surface-border border-b last:border-0",
            )}
          >
            <span className={badgeClass}>协作需求</span>
            <div className="min-w-0 grow basis-40 wrap-anywhere">
              <h3 className="truncate font-medium">
                {row.capability_name}
                {row.project &&
                  ` · ${row.project.title} · ${row.project.role_name}`}
              </h3>
              <p
                className="truncate text-xs text-muted-foreground"
                title={row.description}
              >
                {row.description}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              {row.headcount} 人 · 每人 {row.allocation_percent}% ·{" "}
              {row.window_start} 至 {row.window_end}
            </p>
            <div className="ml-auto flex flex-wrap gap-1">
              <Button size="sm" variant="outline" asChild>
                <Link
                  to="/capacity"
                  search={{ tab: "incoming", demand: row.id }}
                >
                  查看详情
                </Link>
              </Button>
              {employeeId && (
                <Button size="sm" variant="outline" asChild>
                  <Link
                    to="/assistant"
                    search={{
                      mode: "personal",
                      prompt: `请模拟并帮我答复部门需求 ${row.id}（${row.capability_name}，${row.headcount} 人，每人投入 ${row.allocation_percent}%，${row.window_start} 至 ${row.window_end}）。`,
                    }}
                  >
                    用助手拟定方案
                  </Link>
                </Button>
              )}
            </div>
          </article>
        ))
      ) : (
        <p role="status" className="p-3 text-sm text-muted-foreground">
          暂无待答复需求
        </p>
      )}
    </>
  )
}

function ProfileReviews({ data }: { data?: PendingReviews }) {
  return (
    <>
      <p
        role="status"
        className={
          data?.count ? "sr-only" : "p-3 text-sm text-muted-foreground"
        }
      >
        {data?.count ?? 0} 条待审批
      </p>
      <ul className="divide-y divide-surface-border">
        {(data?.data ?? []).map((request) => {
          const via = request.submitted_via
          const source =
            via === "mcp"
              ? "外部 AI（MCP）"
              : via === "web"
                ? "网页提交"
                : via && viaLabels[via]
          return (
            <li key={request.id} className={rowClass}>
              <span className={cn(badgeClass, "bg-primary/10 text-primary")}>
                画像变更
              </span>
              <h3 className="min-w-0 grow basis-40 wrap-anywhere font-medium">
                {request.employee_name}
                <span className="ml-2 font-normal text-muted-foreground">
                  {request.department_name ?? "未分配部门"}
                </span>
              </h3>
              <span>{request.item_count} 项改动</span>
              {!request.owner_reviewed && (
                <span className="text-xs text-amber-700 dark:text-amber-300">
                  员工未确认
                </span>
              )}
              {source && (
                <span
                  className={cn(
                    badgeClass,
                    "wrap-anywhere",
                    via === "mcp" &&
                      "border-violet-500/20 bg-violet-500/10 text-violet-700 dark:text-violet-300",
                    via === "bot" && "bg-primary/10 text-primary",
                  )}
                >
                  {source}
                  {request.client_name &&
                    ` · ${requestClientName(request.client_name, via)}`}
                </span>
              )}
              {request.submitted_at && (
                <time
                  dateTime={request.submitted_at}
                  className="text-xs whitespace-nowrap text-muted-foreground"
                >
                  {new Date(request.submitted_at).toLocaleString("zh-CN")}
                </time>
              )}
              {request.can_decide ? (
                <Button size="sm" variant="outline" className="ml-auto" asChild>
                  <Link
                    to="/reviews/$requestId"
                    params={{ requestId: request.id }}
                  >
                    查看并审批
                  </Link>
                </Button>
              ) : (
                <p
                  role="status"
                  className="ml-auto text-xs text-muted-foreground"
                >
                  {request.unavailable_reason}
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </>
  )
}
