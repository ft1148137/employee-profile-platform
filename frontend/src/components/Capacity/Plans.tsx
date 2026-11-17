import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useLocation, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useState } from "react"
import { type CasePublic, PlanningService, type ReviseInput } from "@/client"
import { ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { isCapacityAccessDenied } from "./shared"

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          typeof item === "object" && item !== null && !Array.isArray(item),
      )
    : []
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function label(value: unknown, fallback = "未定"): string {
  return typeof value === "string" ? value : fallback
}

function number(value: unknown): string {
  return typeof value === "number" ? String(value) : "未定"
}

function errorText(error: Error): string {
  if (isCapacityAccessDenied(error)) return "你现在没有权限查看这部分"
  if (error instanceof AxiosError) {
    const detail: unknown = error.response?.data?.detail
    if (typeof detail === "string") return detail
    const messages = records(detail).map((item) => {
      const message = label(item.msg, "")
      if (/[\u3400-\u9fff]/.test(message))
        return message.replace(/^Value error, /, "")
      if (strings(item.loc).includes("allocation_percent"))
        return "投入比例须为 10 至 100 的整数"
      return "修改内容格式不正确，请检查后重试"
    })
    if (messages.length) return messages.join("；")
  }
  return "方案暂时无法读取，请重试"
}

function weekLabel(value: unknown): string | null {
  if (typeof value !== "string") return null
  const [, month, day] = value.split("-")
  return month && day ? `${Number(month)} 月 ${Number(day)} 日那一周` : null
}

function findingText(
  finding: Record<string, unknown>,
  people: Map<string, string>,
): string {
  const person =
    typeof finding.employee_id === "string"
      ? `${people.get(finding.employee_id) ?? "待确认人员"}：`
      : ""
  const week = weekLabel(finding.week_start)
  return `${person}${label(finding.message)}${week ? ` · ${week}` : ""}`
}

function RevisionControls({
  option,
  plan,
  orgId,
  onRevised,
}: {
  option: CasePublic["options"][number]
  plan: CasePublic
  orgId: string
  onRevised: () => Promise<unknown>
}) {
  const [change, setChange] = useState<ReviseInput["change"] | null>(null)
  const [employeeId, setEmployeeId] = useState("")
  const [replacementId, setReplacementId] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [percent, setPercent] = useState(20)
  const [splitDate, setSplitDate] = useState("")
  const assignees = records(option.params.assignees)
  const locks = new Set(strings(option.params.locked_employee_ids))
  const selected = new Set(assignees.map((item) => label(item.employee_id)))
  const people = new Map(
    plan.people.map((person) => [person.employee_id, person.display_name]),
  )
  const candidates = records(plan.data.candidates)
    .map((item) => label(item.employee_id, ""))
    .filter((id) => people.has(id) && !selected.has(id))
  const selectedIds = [...selected].filter((id) => people.has(id))
  const editable = selectedIds.filter((id) => !locks.has(id))
  const mutation = useMutation({
    mutationFn: async (body: ReviseInput) =>
      PlanningService.reviseOption({
        path: { org_id: orgId, case_id: plan.id, option_id: option.id },
        body,
      }),
    onSuccess: async () => {
      setChange(null)
      await onRevised()
    },
  })
  if (!assignees.length || plan.stale) return null
  const submit = () => {
    let body: ReviseInput
    if (change === "replace") {
      body = {
        change,
        from_employee_id: employeeId,
        to_employee_id: replacementId,
      }
    } else if (change === "dates") {
      body = {
        change,
        start_date: startDate,
        end_date: endDate,
        ...(employeeId ? { employee_id: employeeId } : {}),
      }
    } else if (change === "allocation") {
      body = {
        change,
        allocation_percent: percent,
        ...(employeeId ? { employee_id: employeeId } : {}),
      }
    } else if (change === "phased") {
      body = { change, split_date: splitDate }
    } else if (change === "lock" || change === "unlock") {
      body = { change, employee_ids: [employeeId] }
    } else return
    mutation.mutate(body)
  }
  const choose = (next: ReviseInput["change"]) => {
    setChange(next)
    setEmployeeId("")
    setReplacementId("")
    setStartDate(label(assignees[0]?.start_date, ""))
    setEndDate(label(assignees[0]?.end_date, ""))
    setSplitDate("")
    mutation.reset()
  }
  const choices =
    change === "unlock"
      ? selectedIds.filter((id) => locks.has(id))
      : change === "lock"
        ? editable
        : editable
  return (
    <div className="space-y-2 border-t pt-3">
      <p className="font-medium">快捷修改</p>
      <div className="flex flex-wrap gap-2">
        {(
          [
            "replace",
            "lock",
            "unlock",
            "dates",
            "allocation",
            "phased",
          ] as const
        ).map((action) => (
          <Button
            key={action}
            size="sm"
            variant="outline"
            onClick={() => choose(action)}
            disabled={
              (action === "replace" &&
                (!editable.length || !candidates.length)) ||
              (["lock", "dates", "allocation"].includes(action) &&
                !editable.length) ||
              (action === "unlock" && !locks.size) ||
              (action === "phased" && !!locks.size)
            }
          >
            {
              {
                replace: "换人",
                lock: "锁定",
                unlock: "解锁",
                dates: "改时间",
                allocation: "降低投入",
                phased: "分阶段交付",
              }[action]
            }
          </Button>
        ))}
      </div>
      {!editable.length && <p>请先解锁，再改时间或降低投入</p>}
      {change && (
        <div className="flex flex-wrap items-end gap-2">
          {change !== "phased" && (
            <label>
              人员
              <select
                aria-label="修改人员"
                className="block rounded border p-2"
                value={employeeId}
                onChange={(event) => setEmployeeId(event.target.value)}
              >
                {(change === "dates" || change === "allocation") && (
                  <option value="">所有未锁定人员</option>
                )}
                {(change === "replace" ||
                  change === "lock" ||
                  change === "unlock") && <option value="">选择人员</option>}
                {choices.map((id) => (
                  <option key={id} value={id}>
                    {people.get(id)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {change === "replace" && (
            <label>
              换成
              <select
                aria-label="换成谁"
                className="block rounded border p-2"
                value={replacementId}
                onChange={(event) => setReplacementId(event.target.value)}
              >
                <option value="">选择候选人</option>
                {candidates.map((id) => (
                  <option key={id} value={id}>
                    {people.get(id)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {change === "dates" && (
            <>
              <label>
                开始日期
                <input
                  aria-label="新的开始日期"
                  className="block rounded border p-2"
                  type="date"
                  value={startDate}
                  onChange={(event) => setStartDate(event.target.value)}
                />
              </label>
              <label>
                结束日期
                <input
                  aria-label="新的结束日期"
                  className="block rounded border p-2"
                  type="date"
                  value={endDate}
                  onChange={(event) => setEndDate(event.target.value)}
                />
              </label>
            </>
          )}
          {change === "allocation" && (
            <label>
              投入比例
              <input
                aria-label="新的投入比例"
                className="block w-24 rounded border p-2"
                type="number"
                min={10}
                max={100}
                value={percent}
                onChange={(event) => setPercent(Number(event.target.value))}
              />
            </label>
          )}
          {change === "phased" && (
            <label>
              第二阶段开始日期（周一）
              <input
                aria-label="第二阶段开始日期"
                className="block rounded border p-2"
                type="date"
                value={splitDate}
                onChange={(event) => setSplitDate(event.target.value)}
              />
            </label>
          )}
          <Button
            size="sm"
            onClick={submit}
            disabled={
              mutation.isPending ||
              ((change === "replace" ||
                change === "lock" ||
                change === "unlock") &&
                !employeeId) ||
              (change === "replace" && !replacementId) ||
              (change === "phased" && !splitDate) ||
              (change === "dates" && (!startDate || !endDate))
            }
          >
            确认修改
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setChange(null)}>
            取消
          </Button>
        </div>
      )}
      {mutation.error && <p role="alert">{errorText(mutation.error)}</p>}
    </div>
  )
}

function OptionCard({
  option,
  plan,
  orgId,
  onRevised,
}: {
  option: CasePublic["options"][number]
  plan: CasePublic
  orgId: string
  onRevised: () => Promise<unknown>
}) {
  const people = new Map(
    plan.people.map((person) => [person.employee_id, person.display_name]),
  )
  const assignments = new Map(
    records(plan.data.assignments).map((item) => [item.assignment_id, item]),
  )
  const assignees = records(option.params.assignees)
  const affected = strings(option.summary.affected_assignment_ids)
  const locks = new Set(strings(option.params.locked_employee_ids))
  const base = plan.options.find(
    (item) => item.id === option.summary.base_option_id,
  )
  const diff = records([option.summary.diff])[0]
  return (
    <article
      className="space-y-4 rounded-xl border p-5"
      aria-label={`${option.kind_label}方案`}
    >
      <h4 className="text-lg font-semibold">
        {option.kind_label} · {option.verdict_label}
      </h4>
      {base && (
        <div className="rounded bg-muted p-3">
          <p>
            在「{base.kind_label}方案」上修改：{label(option.summary.changes)}
          </p>
          {diff && (
            <div>
              <p>这次改了什么</p>
              <p>
                结论：{base.verdict_label} → {option.verdict_label}
              </p>
              <p>
                交付日期：{label(records([diff.finish_date])[0]?.before)} →{" "}
                {label(records([diff.finish_date])[0]?.after)}
              </p>
              <p>
                覆盖人数：{number(records([diff.covered_headcount])[0]?.before)}{" "}
                → {number(records([diff.covered_headcount])[0]?.after)}
              </p>
              <p>
                缺口：{number(records([diff.gap_person_weeks])[0]?.before)} →{" "}
                {number(records([diff.gap_person_weeks])[0]?.after)} 人周
              </p>
              <p>
                新增风险：
                {records(diff.added_findings)
                  .map((item) => findingText(item, people))
                  .join("；") || "无"}
              </p>
              <p>
                解决风险：
                {records(diff.resolved_findings)
                  .map((item) => findingText(item, people))
                  .join("；") || "无"}
              </p>
            </div>
          )}
        </div>
      )}
      <p>
        交付日期：{label(option.summary.finish_date)} · 覆盖{" "}
        {number(option.summary.covered_headcount)} 人 · 还差{" "}
        {number(option.summary.gap_person_weeks)} 人周
      </p>
      <div>
        <h5 className="font-medium">人员安排</h5>
        {assignees.length ? (
          <ul className="list-inside list-disc">
            {assignees.map((person, index) => (
              <li key={`${option.id}-${index}`}>
                {people.get(label(person.employee_id)) ?? "待确认人员"} · 投入{" "}
                {number(person.allocation_percent)}% ·{label(person.start_date)}{" "}
                至 {label(person.end_date)}
                {locks.has(label(person.employee_id)) && " · 已锁定"}
              </li>
            ))}
          </ul>
        ) : (
          <p>尚未确定具体人员</p>
        )}
        {records(option.params.departments).map((department, index) => (
          <p key={index}>候选部门：{label(department.department_name)}</p>
        ))}
      </div>
      <div>
        <h5 className="font-medium">受影响的工作</h5>
        {affected.length ? (
          <ul className="list-inside list-disc">
            {affected.map((id) => {
              const work = assignments.get(id)
              if (!work) return null
              return (
                <li key={id}>
                  {people.get(label(work.employee_id)) ?? "待确认人员"} ·
                  {work.start_date === null
                    ? "开始日期未确认"
                    : label(work.start_date)}
                  （计入日期 {label(work.counted_from)}）至{" "}
                  {label(work.due_date)} · 已投入{" "}
                  {number(work.allocation_percent)}%
                </li>
              )
            })}
          </ul>
        ) : (
          <p>暂无受影响的进行中工作</p>
        )}
      </div>
      <div>
        <h5 className="font-medium">风险</h5>
        {option.findings.length ? (
          <ul className="list-inside list-disc">
            {option.findings.map((finding, index) => (
              <li key={index}>{findingText(finding, people)}</li>
            ))}
          </ul>
        ) : (
          <p>暂无已发现风险</p>
        )}
      </div>
      <div>
        <h5 className="font-medium">条件</h5>
        {strings(option.summary.conditions).length ? (
          <ul className="list-inside list-disc">
            {strings(option.summary.conditions).map((condition) => (
              <li key={condition}>{condition}</li>
            ))}
          </ul>
        ) : (
          <p>无附加条件</p>
        )}
      </div>
      <div>
        <h5 className="font-medium">需要谁确认</h5>
        <ul className="list-inside list-disc">
          {strings(option.summary.approvers).map((approver) => (
            <li key={approver}>{approver}</li>
          ))}
        </ul>
      </div>
      <RevisionControls
        option={option}
        plan={plan}
        orgId={orgId}
        onRevised={onRevised}
      />
    </article>
  )
}

export function Plans({
  orgId,
  demandId,
}: {
  orgId: string
  demandId: string
}) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const hash = useLocation({ select: (location) => location.hash })
  const key = ["capacity", orgId, "planning", demandId, hash]
  const storageKey = `planning-case:${orgId}:${demandId}`
  const create = async () => {
    const { data } = await PlanningService.createCase({
      path: { org_id: orgId, demand_id: demandId },
    })
    localStorage.setItem(storageKey, data.id)
    return data
  }
  const current = useQuery({
    queryKey: key,
    queryFn: async () => {
      if (hash) {
        const caseId = new URLSearchParams(hash).get("case")
        if (!caseId) throw new Error("方案链接无效")
        const { data } = await PlanningService.getCase({
          path: { org_id: orgId, case_id: caseId },
        })
        if (data.demand_id !== demandId) throw new Error("方案不属于当前需求")
        localStorage.setItem(storageKey, data.id)
        return data
      }
      const caseId = localStorage.getItem(storageKey)
      if (caseId) {
        try {
          return (
            await PlanningService.getCase({
              path: { org_id: orgId, case_id: caseId },
            })
          ).data
        } catch (error) {
          if (!(error instanceof AxiosError) || error.response?.status !== 404)
            throw error
        }
      }
      return create()
    },
  })
  const simulation = useMutation({
    mutationFn: create,
    onSuccess: async (data) => {
      const nextHash = hash ? `case=${data.id}` : ""
      client.setQueryData(
        ["capacity", orgId, "planning", demandId, nextHash],
        data,
      )
      if (hash !== nextHash) {
        await navigate({ search: true, hash: nextHash, replace: true })
      } else {
        await current.refetch()
      }
    },
  })
  const plan = current.data
  const error = simulation.error ?? current.error
  if (isCapacityAccessDenied(error))
    return <p role="alert">你现在没有权限查看这部分</p>
  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">协调方案</h3>
      {current.isPending && <ProfileLoading />}
      {error && <p role="alert">{errorText(error)}</p>}
      {error && (!plan || current.error) && (
        <Button
          variant="outline"
          onClick={() => simulation.mutate()}
          disabled={simulation.isPending || current.isFetching}
        >
          重新模拟
        </Button>
      )}
      {plan && !current.error && (
        <>
          {plan.stale && <p role="alert">数据已变化，请重新模拟</p>}
          <Button
            variant="outline"
            onClick={() => simulation.mutate()}
            disabled={simulation.isPending || current.isFetching}
          >
            重新模拟
          </Button>
          {plan.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <div className="grid gap-4 lg:grid-cols-2">
            {plan.options.map((option) => (
              <OptionCard
                key={option.id}
                option={option}
                plan={plan}
                orgId={orgId}
                onRevised={() => current.refetch()}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
