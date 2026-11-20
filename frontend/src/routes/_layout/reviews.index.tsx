import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"
import type { DemandPublic, PendingReviews } from "@/client"
import { Approvals } from "@/components/Capacity/Approvals"
import { BorrowRequestCard } from "@/components/Capacity/BorrowRequests"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { viaLabels } from "@/components/Profile/RequestTimeline"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import {
  PENDING_DEMAND_PAGE_SIZE,
  pendingDemandPageOptions,
  usePendingApprovals,
} from "@/hooks/usePendingApprovals"

const filters = {
  all: "全部",
  profile: "画像变更",
  workload: "负荷与延期",
  demand: "协作需求",
  borrow: "借调确认",
}

type ApprovalType = keyof typeof filters

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
        <BorrowRequestCard
          key={request.id}
          orgId={org.id}
          request={request}
          mine={mine}
        />
      )),
    },
  ] as const
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="space-y-4 border-b pb-4">
        <h1 className="text-2xl font-semibold">待审批</h1>
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
              className="flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm hover:bg-muted aria-[current=page]:border-primary aria-[current=page]:bg-primary/10 aria-[current=page]:text-primary"
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
              className="space-y-3"
            >
              <h2 className="text-lg font-semibold">{filters[section.type]}</h2>
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
                <p role="status" className="text-muted-foreground">
                  暂无待审批事项
                </p>
              )}
            </section>
          )
        })}
    </div>
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
          className="flex items-center justify-between gap-2"
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
          <article key={row.id} className="space-y-2 rounded-xl border p-5">
            <h3 className="font-semibold">{row.capability_name} · 待答复</h3>
            {row.project && (
              <p className="break-words text-sm">
                项目：{row.project.title} · 角色：{row.project.role_name}
              </p>
            )}
            <p className="whitespace-pre-wrap break-words">{row.description}</p>
            <p className="text-sm text-muted-foreground">
              {row.headcount} 人 · 每人 {row.allocation_percent}% ·{" "}
              {row.window_start} 至 {row.window_end}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link
                  to="/capacity"
                  search={{ tab: "incoming", demand: row.id }}
                >
                  查看详情
                </Link>
              </Button>
              {employeeId && (
                <Button variant="outline" asChild>
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
        <p role="status" className="text-muted-foreground">
          暂无待答复需求
        </p>
      )}
    </>
  )
}

function ProfileReviews({ data }: { data?: PendingReviews }) {
  return (
    <>
      <p role="status">{data?.count ?? 0} 条待审批</p>
      <ul className="space-y-4">
        {(data?.data ?? []).map((request) => (
          <li key={request.id} className="rounded-xl border p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="font-semibold">
                {request.employee_name} ·{" "}
                {request.department_name ?? "未分配部门"}
              </h3>
              <span>{request.item_count} 项改动</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {request.submitted_at
                ? new Date(request.submitted_at).toLocaleString("zh-CN")
                : "—"}{" "}
              · {viaLabels[request.submitted_via ?? "web"]}
              {request.client_name && ` · ${request.client_name}`}
            </p>
            {!request.owner_reviewed && (
              <p className="text-sm text-amber-700">员工未确认</p>
            )}
            {request.can_decide ? (
              <Link
                className="inline-block text-primary underline"
                to="/reviews/$requestId"
                params={{ requestId: request.id }}
              >
                查看并审批
              </Link>
            ) : (
              <p role="status">{request.unavailable_reason}</p>
            )}
          </li>
        ))}
      </ul>
    </>
  )
}
