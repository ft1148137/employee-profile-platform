import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import {
  CapacityService,
  type DemandOutcome,
  type DemandPublic,
  type ReplyInput,
  type RespondInput,
} from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DemandsReference } from "./DemandsReference"
import { WorkloadBand } from "./shared"

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
}

export function DemandsDetail({
  orgId,
  demandId,
  side,
  back,
}: {
  orgId: string
  demandId: string
  side: "outgoing" | "incoming"
  back: () => void
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "demand", demandId],
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
        返回列表
      </Button>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
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
          </section>
          <section aria-label="事件时间线" className="space-y-3">
            <h3 className="font-semibold">事件时间线</h3>
            {(query.data.events ?? []).map((event, index) => (
              <article key={index} className="space-y-2 border-l-2 pl-4">
                <p className="font-medium">
                  {events[event.kind]} · {dateLabel(event.created_at)}
                  {event.responder_name && ` · 答复人：${event.responder_name}`}
                </p>
                {event.outcome && <p>结论：{outcomes[event.outcome]}</p>}
                {(event.start_date || event.end_date) && (
                  <p>
                    最早开始：{event.start_date ?? "未定"} · 承诺完成：
                    {event.end_date ?? "未定"}
                  </p>
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
          {side === "incoming" && (
            <DemandsBasis orgId={orgId} demandId={demandId} />
          )}
          <DemandsAction
            key={query.data.revision}
            orgId={orgId}
            demand={query.data}
            side={side}
          />
        </>
      )}
    </div>
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
        <ProfileError retry={() => query.refetch()} />
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
}: {
  orgId: string
  demand: DemandPublic
  side: "outgoing" | "incoming"
}) {
  const cache = useQueryClient()
  const [outcome, setOutcome] = useState<DemandOutcome>("can_do")
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
    mutationFn: (dates: Pick<ReplyInput, "start_date" | "end_date">) =>
      CapacityService.replyDemand({
        path,
        body: {
          ...dates,
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
  if (demand.status === "accepted" || demand.status === "withdrawn") return null
  if (side === "incoming" && demand.status !== "submitted") return null
  const pending = reply.isPending || respond.isPending
  return (
    <form
      aria-label={side === "incoming" ? "答复需求" : "回应答复"}
      className="max-w-3xl space-y-4 rounded-xl border p-5"
      onSubmit={(event) => {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        reply.mutate({
          start_date: String(data.get("start")) || null,
          end_date: String(data.get("end")) || null,
        })
      }}
    >
      <fieldset disabled={pending} className="space-y-4">
        {side === "incoming" && (
          <>
            <h3 className="font-semibold">答复需求</h3>
            <p className="text-sm text-muted-foreground">
              答复会发给对方部门，不要写本部门员工的姓名；在助手里可以让 AI
              先起草
            </p>
            <label className="block space-y-2 text-sm font-medium">
              <span>答复结论</span>
              <select
                value={outcome}
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
      <SetupError error={reply.error || respond.error} />
    </form>
  )
}
