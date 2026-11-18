import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useState } from "react"
import {
  CapacityService,
  type DemandOutcome,
  type DemandPublic,
  type PlanReplyInput,
  type ProposalLinePublic,
  type RespondInput,
} from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { DemandAssignments } from "./DemandAssignments"
import { DemandsReference } from "./DemandsReference"
import { Plans, type SelectedPlanReply } from "./Plans"
import { isCapacityAccessDenied, WorkloadBand } from "./shared"

export const demandStatuses = {
  submitted: "待答复",
  replied: "已答复",
  accepted: "已接受",
  withdrawn: "已撤回",
}

const outcomes: Record<DemandOutcome, string> = {
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
}: {
  orgId: string
  demandId: string
  side: "outgoing" | "incoming"
  back: () => void
  plansOpen?: boolean
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
  return (
    <div className="space-y-5">
      <Button variant="outline" onClick={back}>
        {isCapacityAccessDenied(query.error) ? "返回需求列表" : "返回列表"}
      </Button>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        isCapacityAccessDenied(query.error) ? (
          <p role="status" className="rounded-xl border bg-muted/30 p-5">
            未找到这条需求，或你没有权限查看。需求只有提出人、需求方部门经理和目标部门经理能查看，可以联系他们了解进度。
          </p>
        ) : (
          <ProfileError retry={() => query.refetch()} />
        )
      ) : (
        <>
          <section
            aria-label="需求详情"
            className="space-y-3 rounded-xl border p-5"
          >
            <h2 className="text-xl font-semibold">
              {query.data.capability_name} · {demandStatuses[query.data.status]}
            </h2>
            <p>
              {query.data.headcount} 人 · 每人 {query.data.allocation_percent}%
              · {query.data.window_start} 至 {query.data.window_end}
            </p>
            <p className="whitespace-pre-wrap break-words">
              {query.data.description}
            </p>
            {query.data.committed_end_date && (
              <p>当前承诺完成：{query.data.committed_end_date}</p>
            )}
          </section>
          <section aria-label="事件时间线" className="space-y-3">
            <h3 className="font-semibold">事件时间线</h3>
            {(query.data.events ?? []).map((event, index) => (
              <article key={index} className="space-y-2 border-l-2 pl-4">
                <p className="font-medium">
                  {events[event.kind]} · {dateLabel(event.created_at)}
                  {event.kind === "replied" &&
                    ` · 答复人：${side === "outgoing" ? "对方部门经理" : (event.responder_name ?? "对方部门经理")}`}
                </p>
                {event.outcome && <p>结论：{outcomes[event.outcome]}</p>}
                {event.kind === "commitment_changed" ? (
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
                <p className="whitespace-pre-wrap break-words">{event.text}</p>
                {event.basis && (
                  <div className="rounded-lg bg-muted/30 p-3">
                    <p className="text-sm font-medium">部门级依据（答复时）</p>
                    <DemandsReference reference={event.basis} />
                  </div>
                )}
              </article>
            ))}
          </section>
          {side === "incoming" && !query.data.basis_url && (
            <DemandPlanAccess orgId={orgId} demand={query.data} />
          )}
          {side === "incoming" && query.data.basis_url && (
            <DemandsBasis orgId={orgId} demandId={demandId} />
          )}
          {side === "incoming" && query.data.basis_url && (
            <section aria-label="协调方案" className="space-y-3">
              <Button
                variant="outline"
                onClick={() =>
                  navigate({
                    to: "/capacity",
                    search: {
                      tab: "incoming",
                      demand: demandId,
                      plans: plansOpen ? undefined : 1,
                    },
                  })
                }
              >
                {plansOpen ? "关闭方案" : "生成方案 / 查看方案"}
              </Button>
              {plansOpen && (
                <Plans
                  orgId={orgId}
                  demandId={demandId}
                  onReply={
                    query.data.status === "submitted"
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
              )}
            </section>
          )}
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
          {side === "incoming" &&
            query.data.basis_url &&
            query.data.status === "accepted" && (
              <DemandAssignments orgId={orgId} demand={query.data} />
            )}
          {(side === "outgoing" || query.data.basis_url) && (
            <DemandsAction
              key={`${query.data.revision}:${selected?.option_id ?? ""}`}
              orgId={orgId}
              demand={query.data}
              side={side}
              selected={selected}
            />
          )}
        </>
      )}
    </div>
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
  demandId,
}: {
  orgId: string
  demandId: string
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "demand-basis", demandId],
    queryFn: async () =>
      (
        await CapacityService.getDemandBasis({
          path: { org_id: orgId, demand_id: demandId },
        })
      ).data,
  })
  return (
    <section aria-label="本部门依据" className="space-y-3">
      <h3 className="font-semibold">本部门依据</h3>
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
            达到要求的有 {query.data.count} 人，按姓名排序。Jira
            参考值与经理确认值分别展示。
          </p>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/30">
                <tr>
                  {["姓名", "等级", "Jira 参考值", "经理确认", "负荷状态"].map(
                    (label) => (
                      <th key={label} className="whitespace-nowrap p-3">
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {query.data.data.map((person) => (
                  <tr key={person.employee_id} className="border-t">
                    <th scope="row" className="whitespace-nowrap p-3">
                      {person.display_name}
                    </th>
                    <td className="p-3">{person.level_name ?? "未定级"}</td>
                    <td className="p-3">
                      {person.workload.jira_task_count === null
                        ? "无，未接 Jira 或未关联 Jira 账号"
                        : `${person.workload.jira_task_count} 件未完成`}
                    </td>
                    <td className="p-3">
                      {person.workload.confirmed_percent === null
                        ? "未确认"
                        : `${person.workload.confirmed_percent}%`}
                    </td>
                    <td className="p-3">
                      <WorkloadBand band={person.workload.band} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function DemandsAction({
  orgId,
  demand,
  side,
  selected,
}: {
  orgId: string
  demand: DemandPublic
  side: "outgoing" | "incoming"
  selected: SelectedPlanReply | null
}) {
  const cache = useQueryClient()
  const [outcome, setOutcome] = useState<DemandOutcome>(
    selected?.outcome ?? "can_do",
  )
  const [text, setText] = useState("")
  const path = { org_id: orgId, demand_id: demand.id }
  const refresh = () => {
    cache.invalidateQueries({ queryKey: ["capacity", orgId] })
    cache.invalidateQueries({ queryKey: ["inbox", orgId] })
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
          text: text.trim(),
          revision: demand.revision,
        },
      }),
    onSuccess: refresh,
  })
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
      className="max-w-3xl space-y-4 rounded-xl border p-5"
      onSubmit={(event) => {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        reply.mutate({
          start_date: String(data.get("start")) || null,
          end_date: String(data.get("end")) || null,
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
                方案包含不在依据中的人员，我确认按自己的判断安排
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
              答复会发给对方部门，不要写本部门员工的姓名；不写负荷说法（例如基本满、饱和、有余量），只写能否承接、时间和条件。在助手里可以让
              AI 先起草
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
                {Object.entries(outcomes).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
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
            <Button type="submit" disabled={!text.trim()}>
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

function proposalLabel(proposal: NonNullable<DemandPublic["proposal"]>) {
  if (proposal.status !== "pending" && proposal.borrow_progress)
    return proposal.borrow_progress
  if (proposal.status === "failed")
    return (
      { rejected: "被拒", expired: "已过期", recheck_failed: "复核未通过" }[
        proposal.fail_reason ?? ""
      ] ?? "未达成"
    )
  if (
    proposal.status === "pending" &&
    Date.parse(proposal.hold_expires_at) <= Date.now()
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
  return <SetupError error={error} />
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
  const [action, setAction] = useState<
    "accept" | "follow_up" | "withdraw" | null
  >(null)
  const [reason, setReason] = useState("")
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
  const label = proposalLabel(proposal)
  const pending = proposal.status === "pending" && label !== "已过期"
  const requesterApproved = proposal.approvals.some(
    (approval) => approval.party === "requester",
  )
  const groups = new Map<string, ProposalLinePublic[]>()
  if (side === "incoming" && "lines" in proposal)
    for (const line of proposal.lines) {
      const parts = groups.get(line.employee_id) ?? []
      parts.push(line)
      groups.set(line.employee_id, parts)
    }
  const people = groups.size
    ? Array.from(groups.values(), (parts) => ({
        name: parts[0].employee_name,
        segments: parts,
      }))
    : proposal.participants.map((person) => ({
        name: `第 ${person.number} 人`,
        segments: person.segments,
      }))
  return (
    <section aria-label="方案提议" className="space-y-4 rounded-xl border p-5">
      <h3 className="text-lg font-semibold">方案提议 · {label}</h3>
      <p>
        {proposal.headcount} 人 · {proposal.start_date} 至 {proposal.end_date}
      </p>
      {proposal.status === "pending" && (
        <p>预占到期：{dateLabel(proposal.hold_expires_at)}</p>
      )}
      <p className="whitespace-pre-wrap break-words">
        条件：{proposal.conditions}
      </p>
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
      {proposal.approvals.map((approval, index) => (
        <p key={index} className="whitespace-pre-wrap break-words">
          {{
            resource_manager: "资源部门经理",
            requester: "需求方",
            lender_manager: "借出部门经理",
            employee: "员工本人",
          }[approval.party] ?? "审批方"}
          ：{approval.decision === "approved" ? "同意" : "拒绝"}
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
              复核未通过: "容量、版本或权限已变化，请重新规划。",
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
                    接受方案
                  </Button>
                  <Button
                    variant="outline"
                    disabled={mutation.isPending}
                    onClick={() => {
                      setReason("")
                      setAction("follow_up")
                    }}
                  >
                    拒绝方案
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
        open={action !== null}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setAction(null)
        }}
      >
        <DialogContent>
          <DialogTitle>
            {action === "accept"
              ? "接受方案"
              : action === "follow_up"
                ? "拒绝方案"
                : "撤回提议"}
          </DialogTitle>
          <DialogDescription>
            {action === "accept"
              ? proposal.borrow_progress
                ? "接受后记录需求方同意，原部门经理和员工本人也同意后才生成分配"
                : "接受后对方部门的人员投入会被锁定并生成分配"
              : action === "follow_up"
                ? "拒绝后对方需要重新安排"
                : "撤回后预占将释放，对方无法再接受这条提议"}
          </DialogDescription>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              mutation.mutate()
            }}
            className="space-y-4"
          >
            {action === "follow_up" && (
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
            )}
            <div className="flex gap-3">
              <Button
                type="submit"
                disabled={
                  mutation.isPending ||
                  (action === "follow_up" && !reason.trim())
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
