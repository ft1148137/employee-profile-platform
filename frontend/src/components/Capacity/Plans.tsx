import { useQuery } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { type CasePublic, PlanningService } from "@/client"
import { ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"

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
  if (error instanceof AxiosError) {
    const detail: unknown = error.response?.data?.detail
    if (typeof detail === "string") return detail
  }
  return "方案暂时无法读取，请重试"
}

function weekLabel(value: unknown): string | null {
  if (typeof value !== "string") return null
  const [, month, day] = value.split("-")
  return month && day ? `${Number(month)} 月 ${Number(day)} 日那一周` : null
}

function OptionCard({
  option,
  plan,
}: {
  option: CasePublic["options"][number]
  plan: CasePublic
}) {
  const people = new Map(
    plan.people.map((person) => [person.employee_id, person.display_name]),
  )
  const assignments = new Map(
    records(plan.data.assignments).map((item) => [item.assignment_id, item]),
  )
  const assignees = records(option.params.assignees)
  const affected = strings(option.summary.affected_assignment_ids)
  return (
    <article
      className="space-y-4 rounded-xl border p-5"
      aria-label={`${option.kind_label}方案`}
    >
      <h4 className="text-lg font-semibold">
        {option.kind_label} · {option.verdict_label}
      </h4>
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
              <li key={index}>
                {typeof finding.employee_id === "string" &&
                  `${people.get(finding.employee_id) ?? "待确认人员"}：`}
                {label(finding.message)}
                {weekLabel(finding.week_start) &&
                  ` · ${weekLabel(finding.week_start)}`}
              </li>
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
  const path = { org_id: orgId, demand_id: demandId }
  const created = useQuery({
    queryKey: ["capacity", orgId, "planning", demandId],
    queryFn: async () => (await PlanningService.createCase({ path })).data,
    staleTime: Infinity,
  })
  const current = useQuery({
    queryKey: ["capacity", orgId, "planning-case", created.data?.id],
    queryFn: async () =>
      (
        await PlanningService.getCase({
          path: { org_id: orgId, case_id: created.data!.id },
        })
      ).data,
    enabled: !!created.data,
  })
  const plan = current.data ?? created.data
  const resimulate = async () => {
    const result = await created.refetch()
    if (result.isSuccess && created.data) await current.refetch()
  }
  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">协调方案</h3>
      {(created.isPending || current.isPending) && <ProfileLoading />}
      {(created.error || current.error) && (
        <p role="alert">
          {errorText((created.error ?? current.error) as Error)}
        </p>
      )}
      {(created.error || current.error) && (!plan || current.error) && (
        <Button
          variant="outline"
          onClick={resimulate}
          disabled={created.isFetching}
        >
          重新模拟
        </Button>
      )}
      {plan && !current.error && (
        <>
          {plan.stale && <p role="alert">数据已变化，请重新模拟</p>}
          <Button
            variant="outline"
            onClick={resimulate}
            disabled={created.isFetching || current.isFetching}
          >
            重新模拟
          </Button>
          {plan.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <div className="grid gap-4 lg:grid-cols-2">
            {plan.options.map((option) => (
              <OptionCard key={option.id} option={option} plan={plan} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
