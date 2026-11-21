import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useState } from "react"
import {
  CapabilitiesService,
  CapacityService,
  type CommitmentArrangement,
  type DemandOutcome,
  type DemandPublic,
  type PlanReplyInput,
  type ProposalLinePublic,
  type RespondInput,
} from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { CapacityError } from "./AssignmentFields"
import {
  type RejectionReason,
  RejectionReasonSelect,
  rejectionReasonLabel,
} from "./BorrowRequests"
import { DemandAssignments } from "./DemandAssignments"
import { capabilityLevels } from "./DemandFields"
import { DemandsReference } from "./DemandsReference"
import {
  detailAside,
  detailColumns,
  shortRequestNumber,
} from "./PlanningWorkbench"
import { Plans, type SelectedPlanReply } from "./Plans"
import {
  addWeeks,
  isCapacityAccessDenied,
  WorkloadBand,
  weekLoadLabel,
} from "./shared"

export const demandStatuses = {
  submitted: "待答复",
  replied: "已答复",
  accepted: "已接受",
  withdrawn: "已撤回",
}

export const demandOutcomes: Record<DemandOutcome, string> = {
  can_do: "能做",
  partial: "能做一部分",
  not_now: "暂时不能",
}
const events = {
  ...demandStatuses,
  follow_up: "追问",
  commitment_changed: "承诺日期变更",
  committed: "安排已生效",
  proposal_failed: "方案未达成",
}

export function DemandsDetail({
  orgId,
  demandId,
  side,
  back,
  plansOpen = false,
  riskMessage,
}: {
  orgId: string
  demandId: string
  side: "outgoing" | "incoming"
  back: () => void
  plansOpen?: boolean
  riskMessage?: string | null
}) {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<SelectedPlanReply | null>(null)
  const query = useQuery({
    queryKey: ["capacity", orgId, "demand", demandId],
    retry: (count, error) => !isCapacityAccessDenied(error) && count < 3,
    queryFn: async () =>
      (
        await CapacityService.getDemand({
          path: { org_id: orgId, demand_id: demandId },
        })
      ).data,
  })
  const catalog = useQuery({
    queryKey: ["capabilities", orgId],
    enabled: !!query.data?.minimum_level && !query.isError,
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
  })
  const capability = catalog.data?.data.find(
    (item) => item.key === query.data?.capability_key,
  )
  const level = capabilityLevels(capability?.level_rubric).find(
    (level) => level.key === query.data?.minimum_level,
  )?.label
  const minimumLevel =
    typeof level === "string"
      ? level
      : query.data?.minimum_level
        ? catalog.isPending
          ? "正在加载等级"
          : "等级名称不可用"
        : undefined
  const prerequisites = query.data?.project?.prerequisites ?? []
  const change =
    side === "incoming" &&
    query.data?.status === "accepted" &&
    query.data.proposal?.status !== "pending" &&
    (query.data.proposal?.kind === "change" ||
      query.data.proposal?.status === "committed")
  return (
    <div className="space-y-5">
      <Button variant="outline" onClick={back}>
        {side === "incoming" ? "返回待审批" : "返回需求入口"}
      </Button>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        isCapacityAccessDenied(query.error) ? (
          <p role="status" className="rounded-xl border bg-muted/30 p-5">
            未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。
          </p>
        ) : (
          <ProfileError retry={() => query.refetch()} />
        )
      ) : (
        <section aria-label="需求详情" className={detailColumns}>
          <div className="min-w-0 space-y-3">
            <SectionCard className="space-y-2">
              <p
                className="font-mono text-xs text-muted-foreground"
                title={demandId}
              >
                {shortRequestNumber(demandId)}
              </p>
              <h2 className="break-words text-lg font-semibold">
                {query.data.capability_name}
              </h2>
              <p className="whitespace-pre-wrap break-words text-sm">
                {query.data.description}
              </p>
              <Button
                size="sm"
                variant="link"
                className="px-0 xl:hidden"
                onClick={() =>
                  document.getElementById("demand-properties")?.scrollIntoView()
                }
              >
                查看需求属性与操作
              </Button>
            </SectionCard>
            {riskMessage && (
              <p
                role="status"
                className="wrap-anywhere rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm"
              >
                {riskMessage}
              </p>
            )}
            {side === "incoming" && !query.data.basis_url && (
              <DemandPlanAccess orgId={orgId} demand={query.data} />
            )}
            {side === "incoming" && query.data.basis_url && (
              <DemandsBasis orgId={orgId} demand={query.data} />
            )}
            {side === "incoming" && query.data.basis_url && plansOpen && (
              <SectionCard
                id="capacity-plans"
                aria-label="协调方案"
                className="scroll-mt-20 space-y-3"
              >
                <Plans
                  change={change}
                  orgId={orgId}
                  demandId={demandId}
                  onReply={
                    query.data.status === "submitted" || change
                      ? (reply) => {
                          setSelected(reply)
                          requestAnimationFrame(() =>
                            document
                              .getElementById("demand-reply")
                              ?.scrollIntoView({ behavior: "smooth" }),
                          )
                        }
                      : undefined
                  }
                />
              </SectionCard>
            )}
            {side === "incoming" &&
              query.data.basis_url &&
              query.data.status === "accepted" && (
                <DemandAssignments
                  orgId={orgId}
                  demand={query.data}
                  canProposeChange={change}
                />
              )}
            <SectionCard aria-label="事件时间线" className="space-y-3 text-sm">
              <h3 className="font-semibold">事件时间线</h3>
              {(query.data.events ?? []).map((event, index) => (
                <article
                  key={index}
                  className="space-y-1 border-surface-border border-l-2 pl-3"
                >
                  <p className="font-medium">
                    {event.kind === "commitment_changed" && event.start_date
                      ? "安排变更"
                      : events[event.kind]}{" "}
                    · {dateLabel(event.created_at)}
                    {event.kind === "replied" &&
                      ` · 答复人：${side === "outgoing" ? "对方部门经理" : (event.responder_name ?? "对方部门经理")}`}
                  </p>
                  {event.outcome && (
                    <p>结论：{demandOutcomes[event.outcome]}</p>
                  )}
                  {event.kind === "commitment_changed" &&
                  event.previous_end_date !== event.end_date ? (
                    <p>
                      承诺日期由 {event.previous_end_date} 改为 {event.end_date}
                    </p>
                  ) : (
                    (event.start_date || event.end_date) && (
                      <p>
                        最早开始：{event.start_date ?? "未定"} · 承诺完成：
                        {event.end_date ?? "未定"}
                      </p>
                    )
                  )}
                  <p className="whitespace-pre-wrap break-words">
                    {event.text}
                  </p>
                  {event.basis && (
                    <div className="rounded-lg bg-muted/30 p-3">
                      <p className="text-sm font-medium">答复时人员概况</p>
                      <DemandsReference reference={event.basis} />
                    </div>
                  )}
                </article>
              ))}
            </SectionCard>
            <DecisionHistory orgId={orgId} demandId={demandId} />
          </div>
          <aside
            aria-label="需求属性与操作"
            id="demand-properties"
            className={`${detailAside} scroll-mt-16`}
          >
            <SectionCard className="space-y-3 break-words text-sm">
              <h3 className="font-semibold">需求属性与操作</h3>
              <dl className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">当前状态</dt>
                  <dd>
                    <Badge variant="secondary">
                      {demandStatuses[query.data.status]}
                    </Badge>
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">起止周期</dt>
                  <dd>
                    {query.data.window_start} 至 {query.data.window_end}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">人数 / 投入</dt>
                  <dd>
                    {query.data.headcount} 人 · 每人{" "}
                    {query.data.allocation_percent}%
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">能力要求</dt>
                  <dd>
                    <ul className="space-y-1">
                      <li>
                        {query.data.capability_name} · 必需
                        {minimumLevel && ` · ${minimumLevel}`}
                      </li>
                      {(query.data.extra_capabilities ?? []).map((extra) => {
                        const minimum =
                          extra.minimum_level_label ?? extra.minimum_level
                        return (
                          <li key={extra.key}>
                            {extra.capability_name} ·{" "}
                            {extra.required ? "必需" : "可选"}
                            {minimum && ` · ${minimum}`}
                          </li>
                        )
                      })}
                    </ul>
                  </dd>
                </div>
              </dl>
              {query.data.project && (
                <div className="space-y-2 border-surface-border border-y py-2">
                  <p>
                    所属项目 ·{" "}
                    <Link
                      className="font-medium underline underline-offset-4"
                      to="/capacity"
                      search={{
                        tab: "projects",
                        project: query.data.project.id,
                      }}
                    >
                      {query.data.project.title}
                    </Link>
                  </p>
                  <p>角色 · {query.data.project.role_name}</p>
                  {prerequisites.length > 0 && (
                    <ul aria-label="前置角色" className="space-y-1">
                      {prerequisites.map((role) => (
                        <li key={role.id}>
                          前置角色 · {role.role_name}（
                          {role.target_department_name}） ·{" "}
                          {demandStatuses[role.status]} ·{" "}
                          {role.committed_start_date ?? role.window_start} 至{" "}
                          {role.committed_end_date ?? role.window_end}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {query.data.committed_end_date && (
                <p>当前承诺完成：{query.data.committed_end_date}</p>
              )}
              {side === "incoming" && query.data.basis_url && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await navigate({
                      to: "/capacity",
                      resetScroll: false,
                      search: {
                        tab: "incoming",
                        demand: demandId,
                        plans: plansOpen ? undefined : 1,
                      },
                    })
                    if (!plansOpen)
                      document
                        .getElementById("capacity-plans")
                        ?.scrollIntoView()
                  }}
                >
                  {plansOpen
                    ? "关闭方案"
                    : change
                      ? "发起变更"
                      : "生成方案 / 查看方案"}
                </Button>
              )}
              {side === "incoming" &&
                query.data.basis_url &&
                query.data.status === "accepted" && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      document
                        .getElementById("demand-assignments")
                        ?.scrollIntoView()
                    }
                  >
                    查看分配与反馈
                  </Button>
                )}
            </SectionCard>
            {query.data.proposal && (
              <ProposalPanel
                orgId={orgId}
                demand={query.data}
                side={side}
                replan={() => {
                  setSelected(null)
                  void navigate({
                    to: "/capacity",
                    search: { tab: "incoming", demand: demandId, plans: 1 },
                  })
                }}
              />
            )}
            {(side === "outgoing" || query.data.basis_url) && (
              <DemandsAction
                key={`${query.data.revision}:${selected?.option_id ?? ""}`}
                orgId={orgId}
                demand={query.data}
                side={side}
                selected={selected}
                change={change}
                clearSelection={() => setSelected(null)}
              />
            )}
          </aside>
        </section>
      )}
    </div>
  )
}

const approvalParties: Record<string, string> = {
  resource_manager: "资源部门经理",
  requester: "需求方",
  lender_manager: "借出部门经理",
  employee: "员工本人",
}

const failureLabels: Record<string, string> = {
  rejected: "被拒",
  expired: "已过期",
  recheck_failed: "复核未通过",
  borrow_unsuccessful: "借调未达成",
}

function DecisionHistory({
  orgId,
  demandId,
}: {
  orgId: string
  demandId: string
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "history", demandId],
    retry: (count, error) => !isCapacityAccessDenied(error) && count < 3,
    queryFn: async () =>
      (
        await CapacityService.getDemandHistory({
          path: { org_id: orgId, demand_id: demandId },
        })
      ).data,
  })
  return (
    <section aria-label="决策记录" className="space-y-3 rounded-xl border p-5">
      <h3 className="font-semibold">决策记录</h3>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <>
          {query.data.proposals.map((proposal) => (
            <article key={proposal.id} className="space-y-1 border-l-2 pl-4">
              <h4 className="font-medium">
                {proposal.kind === "change" ? "安排变更提议" : "方案提议"} ·{" "}
                {failureLabels[proposal.fail_reason ?? ""] ??
                  proposalStatuses[proposal.status]}{" "}
                · {dateLabel(proposal.created_at)}
              </h4>
              {proposal.approvals.map((approval, index) => (
                <p key={index} className="whitespace-pre-wrap break-words">
                  {approvalParties[approval.party] ?? "审批方"}：
                  {approval.decision === "approved"
                    ? "同意"
                    : `拒绝 · ${rejectionReasonLabel(approval.reason_code)}`}
                  {approval.reason && ` · ${approval.reason}`}
                </p>
              ))}
            </article>
          ))}
          {!!query.data.snapshots?.length && (
            <details>
              <summary className="cursor-pointer font-medium">
                历史方案结论
              </summary>
              {query.data.snapshots.map((snapshot) => (
                <div key={snapshot.id} className="mt-2 border-l-2 pl-4">
                  <p>{dateLabel(snapshot.created_at)}</p>
                  {snapshot.options?.map((option, index) => (
                    <p key={index}>
                      {option.kind_label} · {option.verdict_label}
                    </p>
                  ))}
                </div>
              ))}
            </details>
          )}
          <details
            open={query.data.events.some(
              (event) => event.outcome === "not_now",
            )}
          >
            <summary className="cursor-pointer font-medium">需求时间线</summary>
            {query.data.events.map((event, index) => (
              <div key={index} className="mt-2 border-l-2 pl-4">
                <p>
                  {events[event.kind]} · {dateLabel(event.created_at)}
                  {event.outcome && ` · ${demandOutcomes[event.outcome]}`}
                  {event.outcome === "not_now" &&
                    ` · ${rejectionReasonLabel(event.reason_code)}`}
                </p>
                {event.text && (
                  <p className="whitespace-pre-wrap break-words">
                    {event.text}
                  </p>
                )}
              </div>
            ))}
          </details>
        </>
      )}
    </section>
  )
}

function DemandPlanAccess({
  orgId,
  demand,
}: {
  orgId: string
  demand: DemandPublic
}) {
  const navigate = useNavigate()
  const departments = useQuery({
    queryKey: ["capacity", orgId, "departments"],
    queryFn: async () =>
      (
        await CapacityService.listCapacityDepartments({
          path: { org_id: orgId },
        })
      ).data,
  })
  const name =
    departments.data?.data.find((row) => row.id === demand.target_department_id)
      ?.name ?? "目标部门"
  return (
    <section
      aria-label="方案查看范围"
      className="space-y-3 rounded-xl border bg-muted/30 p-5"
    >
      <p>这条需求由 {name} 答复和安排人员，方案和分配只有该部门经理能看。</p>
      <Button
        variant="outline"
        onClick={() =>
          navigate({
            to: "/capacity",
            search: { tab: "outgoing", demand: demand.id },
          })
        }
      >
        到我提的需求查看进度
      </Button>
    </section>
  )
}

function DemandsBasis({
  orgId,
  demand,
}: {
  orgId: string
  demand: DemandPublic
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "demand-basis", demand.id],
    queryFn: async () =>
      (
        await CapacityService.getDemandBasis({
          path: { org_id: orgId, demand_id: demand.id },
        })
      ).data,
  })
  return (
    <SectionCard aria-label="本部门匹配人员" className="space-y-3">
      <h3 className="font-semibold">本部门匹配人员</h3>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        isCapacityAccessDenied(query.error) ? (
          <p role="status">你现在没有权限查看这部分</p>
        ) : (
          <ProfileError retry={() => query.refetch()} />
        )
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            共 {query.data.count} 名符合条件的员工 · 需求投入{" "}
            {demand.allocation_percent}% / 人
          </p>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/30">
                <tr>
                  {[
                    "候选人",
                    "能力核验",
                    "Jira 参考值",
                    "已确认投入",
                    "需求周期内的周负荷",
                  ].map((label) => (
                    <th key={label} className="whitespace-nowrap px-3 py-2">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {query.data.data.map((person) => {
                  const weeks = person.workload.weeks.filter(
                    ({ week_start }) =>
                      week_start <= demand.window_end &&
                      addWeeks(week_start, 1) > demand.window_start,
                  )
                  return (
                    <tr key={person.employee_id} className="border-t">
                      <th scope="row" className="whitespace-nowrap px-3 py-2">
                        {person.display_name}
                      </th>
                      <td className="px-3 py-2">
                        {person.level_name ?? "未定级"} ·{" "}
                        {person.evidence_count} 条证据
                        <StatusBadge
                          kind="assessment_status"
                          value={person.status}
                        />
                      </td>
                      <td className="px-3 py-2">
                        {person.workload.jira_task_count === null
                          ? "暂无"
                          : `${person.workload.jira_task_count} 件未完成`}
                      </td>
                      <td className="px-3 py-2">
                        {person.workload.confirmed_percent === null
                          ? "未确认"
                          : `${person.workload.confirmed_percent}%`}
                        <WorkloadBand band={person.workload.band} />
                      </td>
                      <td className="px-3 py-2">
                        {weeks.length ? (
                          <details>
                            <summary className="cursor-pointer whitespace-nowrap">
                              {weeks.length} 周 · 投入 / 容量
                            </summary>
                            {weeks.map((week) => (
                              <p
                                key={week.week_start}
                                className="whitespace-nowrap"
                              >
                                {week.week_start} · {weekLoadLabel(week)}
                              </p>
                            ))}
                          </details>
                        ) : (
                          <span className="whitespace-nowrap text-muted-foreground">
                            暂无周期内数据
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </SectionCard>
  )
}

function DemandsAction({
  orgId,
  demand,
  side,
  selected,
  change,
  clearSelection,
}: {
  orgId: string
  demand: DemandPublic
  side: "outgoing" | "incoming"
  selected: SelectedPlanReply | null
  change: boolean
  clearSelection: () => void
}) {
  const cache = useQueryClient()
  const [outcome, setOutcome] = useState<DemandOutcome>(
    selected?.outcome ?? "can_do",
  )
  const [text, setText] = useState("")
  const [reasonCode, setReasonCode] = useState<RejectionReason | "">("")
  const path = { org_id: orgId, demand_id: demand.id }
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["capacity", orgId] }),
      cache.invalidateQueries({ queryKey: ["inbox", orgId] }),
    ])
  }
  const respond = useMutation({
    mutationFn: (action: RespondInput["action"]) =>
      CapacityService.respondDemand({
        path,
        body: { action, revision: demand.revision, text: text.trim() || null },
      }),
    onSuccess: refresh,
  })
  const reply = useMutation({
    mutationFn: (
      dates: Pick<PlanReplyInput, "start_date" | "end_date" | "outside_basis">,
    ) =>
      CapacityService.replyDemand({
        path,
        body: {
          ...dates,
          option_id: selected?.option_id,
          lender_department_id: selected?.lender_department_id,
          borrow_headcount: selected?.borrow_headcount,
          outcome,
          reason_code: outcome === "not_now" ? reasonCode || null : undefined,
          text: text.trim(),
          revision: demand.revision,
        },
      }),
    onSuccess: refresh,
  })
  const propose = useMutation({
    mutationFn: (body: { option_id: string; outside_basis: boolean }) =>
      CapacityService.proposeChange({
        path,
        body: {
          ...body,
          reason: text,
          revision: demand.revision,
          lender_department_id: selected?.lender_department_id,
          borrow_headcount: selected?.borrow_headcount,
        },
      }),
    onSuccess: () => {
      clearSelection()
      return refresh()
    },
  })
  if (change && selected?.option_id) {
    const optionId = selected.option_id
    return (
      <form
        id="demand-reply"
        aria-label="发起安排变更"
        className="min-w-0 scroll-mt-16 space-y-3 rounded-lg border border-surface-border bg-surface p-3"
        onSubmit={(event) => {
          event.preventDefault()
          propose.mutate({
            option_id: optionId,
            outside_basis: new FormData(event.currentTarget).has(
              "outside_basis",
            ),
          })
        }}
      >
        <fieldset disabled={propose.isPending} className="space-y-4">
          <label className="block space-y-2 text-sm font-medium">
            <span>变更理由</span>
            <textarea
              required
              maxLength={500}
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="min-h-28 w-full rounded-md border bg-background p-3"
            />
          </label>
          {selected.outside_basis && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="outside_basis" required />
              方案包含未达画像条件的员工，我确认按实际需要安排
            </label>
          )}
          <Button type="submit" disabled={!text.trim()}>
            发送变更提议
          </Button>
        </fieldset>
        <ProposalError error={propose.error} />
      </form>
    )
  }
  const latestReply = [...(demand.events ?? [])]
    .reverse()
    .find((event) => event.kind === "replied")
  if (
    demand.status === "accepted" ||
    demand.status === "withdrawn" ||
    demand.proposal?.status === "pending"
  )
    return null
  if (side === "incoming" && demand.status !== "submitted") return null
  const pending = reply.isPending || respond.isPending
  return (
    <form
      id="demand-reply"
      aria-label={side === "incoming" ? "答复需求" : "回应答复"}
      className="min-w-0 scroll-mt-16 space-y-3 rounded-lg border border-surface-border bg-surface p-3"
      onSubmit={(event) => {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        reply.mutate({
          start_date: String(data.get("start") ?? "") || null,
          end_date: String(data.get("end") ?? "") || null,
          outside_basis: data.has("outside_basis"),
        })
      }}
    >
      <fieldset disabled={pending} className="space-y-4">
        {side === "incoming" && (
          <>
            <h3 className="font-semibold">答复需求</h3>
            {selected?.outside_basis && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" name="outside_basis" required />
                方案包含未达画像条件的员工，我确认按实际需要安排
              </label>
            )}
            {selected && (
              <p>
                {selected.borrow_headcount
                  ? `拟借调 ${selected.borrow_headcount} 人，发送后通知原部门经理选人。`
                  : "已选择协调方案，发送后将预占人员投入，等待需求方确认。"}
              </p>
            )}
            <p className="text-sm text-muted-foreground">
              回复将发送给对方部门：请勿填写员工姓名或个人负荷情况，仅说明能否承接、时间及条件。
            </p>
            <label className="block space-y-2 text-sm font-medium">
              <span>答复结论</span>
              <select
                value={outcome}
                disabled={!!selected}
                onChange={(event) =>
                  setOutcome(event.target.value as DemandOutcome)
                }
                className="h-10 w-full rounded-md border bg-background px-3"
              >
                {Object.entries(demandOutcomes).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {outcome === "not_now" && (
              <RejectionReasonSelect
                value={reasonCode}
                onChange={setReasonCode}
              />
            )}
            <div className="grid gap-2 2xl:grid-cols-2">
              {[
                ["start", "最早开始日期"],
                ["end", "承诺完成日期"],
              ].map(([name, label]) => (
                <label
                  key={name}
                  htmlFor={`reply-${name}`}
                  className="space-y-2 text-sm font-medium"
                >
                  <span>{label}</span>
                  <Input
                    id={`reply-${name}`}
                    name={name}
                    type="date"
                    readOnly={!!selected}
                    defaultValue={
                      (name === "start"
                        ? selected?.start_date
                        : selected?.end_date) ?? ""
                    }
                    required={outcome !== "not_now"}
                  />
                </label>
              ))}
            </div>
          </>
        )}
        {(side === "incoming" || demand.status === "replied") && (
          <label className="block space-y-2 text-sm font-medium">
            <span>{side === "incoming" ? "答复说明" : "追问内容"}</span>
            <textarea
              required={side === "incoming"}
              maxLength={2000}
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="min-h-28 w-full rounded-md border bg-background p-3"
            />
          </label>
        )}
        <div className="flex flex-wrap gap-3">
          {side === "incoming" ? (
            <Button
              type="submit"
              disabled={!text.trim() || (outcome === "not_now" && !reasonCode)}
            >
              确认发送答复
            </Button>
          ) : (
            <>
              {demand.status === "replied" && (
                <>
                  {latestReply && latestReply.outcome !== "not_now" && (
                    <Button
                      type="button"
                      onClick={() => respond.mutate("accept")}
                    >
                      接受答复
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!text.trim()}
                    onClick={() => respond.mutate("follow_up")}
                  >
                    发送追问
                  </Button>
                </>
              )}
              <Button
                type="button"
                variant="outline"
                onClick={() => respond.mutate("withdraw")}
              >
                撤回需求
              </Button>
            </>
          )}
        </div>
      </fieldset>
      <ProposalError error={reply.error || respond.error} />
    </form>
  )
}

const proposalStatuses: Record<string, string> = {
  pending: "等待需求方确认",
  committed: "已生效",
  failed: "未达成",
  withdrawn: "已撤回",
}

function proposalLabel(
  proposal: NonNullable<DemandPublic["proposal"]>,
  now = Date.now(),
) {
  if (proposal.status !== "pending" && proposal.borrow_progress)
    return proposal.borrow_progress
  if (proposal.status === "failed")
    return failureLabels[proposal.fail_reason ?? ""] ?? "未达成"
  if (
    proposal.status === "pending" &&
    Date.parse(proposal.hold_expires_at) <= now
  )
    return "已过期"
  return proposal.borrow_progress ?? proposalStatuses[proposal.status]
}

function ProposalError({ error }: { error: Error | null }) {
  const detail =
    error instanceof AxiosError ? error.response?.data?.detail : null
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const status = proposalStatuses[String(detail.status)]
    return (
      <div
        role="alert"
        className="space-y-2 rounded-lg border border-destructive/40 p-4 text-sm text-destructive"
      >
        <p>
          {typeof detail.message === "string"
            ? detail.message
            : "请刷新后查看当前状态"}
          {status && `，当前状态：${status}`}
        </p>
        {Array.isArray(detail.over_weeks) && (
          <ul>
            {detail.over_weeks.map(
              (
                week: {
                  week_start: string
                  over_percent: number
                  sources?: string[]
                },
                i: number,
              ) => (
                <li key={i}>
                  {week.week_start} 这周超出 {week.over_percent}%
                  {Array.isArray(week.sources) &&
                    week.sources.length > 0 &&
                    `，占用来源：${week.sources.join("、")}`}
                </li>
              ),
            )}
          </ul>
        )}
        {Array.isArray(detail.weeks) && detail.weeks.length > 0 && (
          <>
            <ul>
              {detail.weeks.map(
                (week: { week_start: string; status: string }, i: number) => (
                  <li key={i}>
                    {week.week_start}：
                    {week.status === "stale" ? "需重新确认" : "未确认"}
                  </li>
                ),
              )}
            </ul>
            <a href="/capacity?tab=team" className="underline">
              去确认团队负荷
            </a>
          </>
        )}
      </div>
    )
  }
  return <CapacityError error={error} />
}

function ProposalPanel({
  orgId,
  demand,
  side,
  replan,
}: {
  orgId: string
  demand: DemandPublic
  side: "incoming" | "outgoing"
  replan: () => void
}) {
  const proposal = demand.proposal!
  const cache = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  const expiresAt = Date.parse(proposal.hold_expires_at)
  useEffect(() => {
    if (proposal.status !== "pending" || !Number.isFinite(expiresAt)) return
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.max(0, expiresAt - Date.now()),
    )
    return () => window.clearTimeout(timer)
  }, [proposal.status, expiresAt])
  const [action, setAction] = useState<
    "accept" | "follow_up" | "withdraw" | null
  >(null)
  const [reason, setReason] = useState("")
  const [reasonCode, setReasonCode] = useState<RejectionReason | "">("")
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["capacity", orgId] })
    void cache.invalidateQueries({ queryKey: ["inbox", orgId] })
  }
  const mutation = useMutation({
    mutationFn: async () => {
      if (action === "withdraw")
        return CapacityService.withdrawProposal({
          path: { org_id: orgId, proposal_id: proposal.id },
          body: { revision: proposal.revision },
        })
      return CapacityService.respondDemand({
        path: { org_id: orgId, demand_id: demand.id },
        body: {
          action: action === "accept" ? "accept" : "follow_up",
          revision: demand.revision,
          text: action === "follow_up" ? reason.trim() : null,
          reason_code: action === "follow_up" ? reasonCode || null : undefined,
        },
      })
    },
    onSuccess: () => {
      setAction(null)
      refresh()
    },
    onError: (error) => {
      setAction(null)
      if (error instanceof AxiosError && error.response?.status === 409)
        refresh()
    },
  })
  const label = proposalLabel(proposal, now)
  const pending = proposal.status === "pending" && label !== "已过期"
  useEffect(() => {
    if (!pending && !mutation.isPending) setAction(null)
  }, [pending, mutation.isPending])
  const requesterApproved = proposal.approvals.some(
    (approval) => approval.party === "requester",
  )
  const change = proposal.kind === "change"
  const acceptLabel = change ? "接受变更" : "接受方案"
  const rejectLabel = change ? "拒绝变更" : "拒绝方案"
  return (
    <section
      aria-label={change ? "安排变更提议" : "方案提议"}
      className="space-y-3 rounded-lg border border-surface-border bg-surface p-3 text-sm"
    >
      <h3 className="text-lg font-semibold">
        {change ? "安排变更提议" : "方案提议"} · {label}
      </h3>
      <p>
        {proposal.headcount} 人 · {proposal.start_date} 至 {proposal.end_date}
      </p>
      {proposal.status === "pending" && (
        <p>预占到期：{dateLabel(proposal.hold_expires_at)}</p>
      )}
      <p className="whitespace-pre-wrap break-words">
        {change ? "变更理由" : "条件"}：{proposal.conditions}
      </p>
      {proposal.previous && (
        <section aria-label="变更前" className="space-y-2">
          <h4 className="font-semibold">变更前</h4>
          {side === "outgoing" && (
            <p className="text-sm text-muted-foreground">
              编号用于对应调整前后的人员位置，方便对比变动。
            </p>
          )}
          <ProposalPeople
            arrangement={proposal.previous}
            side={side}
            phase="previous"
          />
        </section>
      )}
      <section
        aria-label={change ? "变更后" : "安排详情"}
        className="space-y-2"
      >
        {change && <h4 className="font-semibold">变更后</h4>}
        <ProposalPeople
          arrangement={{
            participants: proposal.participants,
            lines: "lines" in proposal ? proposal.lines : null,
          }}
          side={side}
          phase={change ? "current" : undefined}
        />
      </section>
      {proposal.approvals.map((approval, index) => (
        <p key={index} className="whitespace-pre-wrap break-words">
          {approvalParties[approval.party] ?? "审批方"}：
          {approval.decision === "approved" ? "同意" : "拒绝"}
          {approval.reason && `，理由：${approval.reason}`}
        </p>
      ))}
      {!pending &&
        proposal.status !== "committed" &&
        !proposal.borrow_progress && (
          <p>
            {{
              被拒: "对方拒绝了方案，需要重新规划。",
              已过期: "预占已到期，请重新规划。",
              复核未通过: "人员负荷或需求信息已变动，请重新规划。",
              已撤回: "提议已撤回，预占已释放。",
            }[label] ?? "方案未达成，请重新规划。"}
          </p>
        )}
      {pending && (
        <div className="flex flex-wrap gap-3">
          {side === "outgoing"
            ? !requesterApproved && (
                <>
                  <Button
                    disabled={mutation.isPending}
                    onClick={() => setAction("accept")}
                  >
                    {acceptLabel}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={mutation.isPending}
                    onClick={() => {
                      setReason("")
                      setReasonCode("")
                      setAction("follow_up")
                    }}
                  >
                    {rejectLabel}
                  </Button>
                </>
              )
            : demand.basis_url && (
                <Button
                  variant="outline"
                  disabled={mutation.isPending}
                  onClick={() => setAction("withdraw")}
                >
                  撤回提议
                </Button>
              )}
        </div>
      )}
      {side === "incoming" &&
        demand.basis_url &&
        !pending &&
        proposal.status !== "committed" && (
          <Button variant="outline" onClick={replan}>
            重新规划
          </Button>
        )}
      <ProposalError error={mutation.error} />
      <Dialog
        open={action !== null && pending}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setAction(null)
        }}
      >
        <DialogContent>
          <DialogTitle>
            {action === "accept"
              ? acceptLabel
              : action === "follow_up"
                ? rejectLabel
                : "撤回提议"}
          </DialogTitle>
          <DialogDescription>
            {action === "accept"
              ? proposal.borrow_progress
                ? "接受后记录需求方同意，原部门经理和员工本人也同意后才生成分配"
                : change
                  ? "接受后将按新方案替换现有安排"
                  : "接受后对方部门的人员投入会被锁定并生成分配"
              : action === "follow_up"
                ? "拒绝后对方需要重新安排"
                : "撤回后预占将释放，对方无法再接受这条提议"}
          </DialogDescription>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              const current = Date.now()
              if (
                !pending ||
                mutation.isPending ||
                proposal.status !== "pending" ||
                (Number.isFinite(expiresAt) && expiresAt <= current)
              ) {
                setNow(current)
                setAction(null)
                return
              }
              mutation.mutate()
            }}
            className="space-y-4"
          >
            {action === "follow_up" && (
              <>
                <RejectionReasonSelect
                  value={reasonCode}
                  onChange={setReasonCode}
                />
                <label className="block space-y-2 text-sm">
                  <span>拒绝理由</span>
                  <textarea
                    required
                    maxLength={500}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    className="min-h-28 w-full rounded-md border bg-background p-3"
                  />
                </label>
              </>
            )}
            <div className="flex gap-3">
              <Button
                type="submit"
                disabled={
                  !pending ||
                  mutation.isPending ||
                  (action === "follow_up" && (!reasonCode || !reason.trim()))
                }
              >
                {action === "accept"
                  ? "确认接受"
                  : action === "follow_up"
                    ? "确认拒绝"
                    : "确认撤回"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => setAction(null)}
              >
                取消
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function participantLabel(
  person: CommitmentArrangement["participants"][number],
  phase?: "previous" | "current",
) {
  const status = "status" in person ? person.status : undefined
  if (status === "replaced")
    return phase === "previous"
      ? `第 ${person.number} 人（将被更换）`
      : `第 ${person.number} 人（已更换为新人员）`
  if (status === "added") return `第 ${person.number} 人（新增）`
  if (status === "removed") return `第 ${person.number} 人（移除）`
  return `第 ${person.number} 人`
}

function ProposalPeople({
  arrangement,
  side,
  phase,
}: {
  arrangement: CommitmentArrangement
  side: "incoming" | "outgoing"
  phase?: "previous" | "current"
}) {
  const groups = new Map<string, ProposalLinePublic[]>()
  if (side === "incoming" && arrangement.lines)
    for (const line of arrangement.lines) {
      const parts = groups.get(line.employee_id) ?? []
      parts.push(line)
      groups.set(line.employee_id, parts)
    }
  const people = groups.size
    ? Array.from(groups.values(), (parts) => ({
        name: parts[0].employee_name,
        segments: parts,
      }))
    : arrangement.participants.map((person) => ({
        name: participantLabel(person, phase),
        segments: person.segments,
      }))
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {people.map((person, index) => (
        <div key={index} className="space-y-2 rounded-lg bg-muted/30 p-3">
          <h4 className="font-medium">{person.name}</h4>
          {person.segments.map((segment, i) => (
            <p key={i} className="text-sm">
              投入 {segment.allocation_percent}% · {segment.start_date} 至{" "}
              {segment.end_date}
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}
