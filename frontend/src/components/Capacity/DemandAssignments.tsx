import { useMutation, useQuery } from "@tanstack/react-query"
import { useId, useState } from "react"
import {
  type AssignmentInput,
  type AssignmentUpdateInput,
  CapacityService,
  type DemandAssignment,
  type DemandPublic,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"
import {
  CapacityError,
  CommitmentFields,
  commitmentBody,
  DateField,
  useCapacityRefresh,
} from "./AssignmentFields"

export const assignmentStatuses = {
  active: "进行中",
  done: "已完成",
  cancelled: "已取消",
}

export function DemandAssignments({
  orgId,
  demand,
}: {
  orgId: string
  demand: DemandPublic
}) {
  const id = useId()
  const refresh = useCapacityRefresh(orgId)
  const { managedDepartmentIds } = useOrg()
  const path = { org_id: orgId, demand_id: demand.id }
  const query = useQuery({
    queryKey: ["capacity", orgId, "assignments", demand.id],
    queryFn: async () =>
      (await CapacityService.listDemandAssignments({ path })).data,
  })
  const basis = useQuery({
    queryKey: ["capacity", orgId, "demand-basis", demand.id],
    queryFn: async () => (await CapacityService.getDemandBasis({ path })).data,
  })
  const create = useMutation({
    mutationFn: (body: AssignmentInput) =>
      CapacityService.createAssignment({ path, body }),
    onSuccess: refresh,
  })
  return (
    <section
      aria-label="部门内分配"
      className="space-y-4 rounded-xl border p-5"
    >
      <h3 className="text-lg font-semibold">部门内分配</h3>
      {basis.data &&
        (!basis.data.data.length && !basis.data.others.length ? (
          <p>本部门没有可分配的在职员工，请联系企业管理员核对部门人员</p>
        ) : basis.data.count < demand.headcount ? (
          <p>
            依据中达到要求的有 {basis.data.count} 人，需要 {demand.headcount}{" "}
            人。可以从本部门其他在职员工中选择；选择不会改变画像，也不会改变依据人数，需求方看到的内容不变。
          </p>
        ) : null)}
      <form
        aria-label="新增分配"
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          const data = new FormData(event.currentTarget)
          create.mutate({
            employee_id: String(data.get("employee_id")),
            allocation_percent: Number(data.get("allocation_percent")),
            due_date: String(data.get("due_date")),
          })
        }}
      >
        <fieldset
          disabled={create.isPending || !basis.isSuccess || !query.isSuccess}
          className="grid gap-3 sm:grid-cols-3"
        >
          <label className="space-y-2 text-sm">
            <span>从依据中选择员工</span>
            <select
              name="employee_id"
              required
              className="h-10 w-full rounded-md border bg-background px-3"
              defaultValue=""
            >
              <option value="" disabled>
                请选择
              </option>
              {[
                { label: "依据中的员工", people: basis.data?.data },
                {
                  label: "本部门其他在职员工（不在依据中）",
                  people: basis.data?.others,
                },
              ].map(({ label, people }) => (
                <optgroup key={label} label={label}>
                  {people
                    ?.filter(
                      (person) =>
                        !query.data?.data.some(
                          (row) =>
                            row.employee_id === person.employee_id &&
                            row.status === "active",
                        ),
                    )
                    .map((person) => (
                      <option
                        key={person.employee_id}
                        value={person.employee_id}
                      >
                        {person.display_name}
                      </option>
                    ))}
                </optgroup>
              ))}
            </select>
          </label>
          <label htmlFor={`${id}-percent`} className="space-y-2 text-sm">
            <span>投入比例（%）</span>
            <Input
              id={`${id}-percent`}
              type="number"
              name="allocation_percent"
              min={10}
              max={100}
              step={1}
              required
              defaultValue={demand.allocation_percent}
            />
          </label>
          <DateField
            label="预计完成日期"
            name="due_date"
            max={demand.committed_end_date ?? undefined}
          />
          <Button type="submit">确认分配</Button>
        </fieldset>
        <CapacityError error={create.error} />
      </form>
      {basis.isError && <ProfileError retry={() => basis.refetch()} />}
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <>
          {!query.data.count && (
            <p className="text-muted-foreground">暂无分配。</p>
          )}
          {query.data.data.map((row) => (
            <AssignmentRow
              key={`${row.id}:${row.due_date}`}
              orgId={orgId}
              row={row}
              committedEndDate={demand.committed_end_date ?? null}
              canEdit={managedDepartmentIds.includes(
                demand.target_department_id,
              )}
            />
          ))}
        </>
      )}
    </section>
  )
}

function AssignmentRow({
  orgId,
  row,
  committedEndDate,
  canEdit,
}: {
  orgId: string
  row: DemandAssignment
  committedEndDate: string | null
  canEdit: boolean
}) {
  const refresh = useCapacityRefresh(orgId)
  const [dueDate, setDueDate] = useState(row.due_date)
  const update = useMutation({
    mutationFn: (body: AssignmentUpdateInput) =>
      CapacityService.updateAssignment({
        path: { org_id: orgId, assignment_id: row.id },
        body,
      }),
    onSuccess: refresh,
  })
  return (
    <article
      aria-label={row.employee_name}
      className="space-y-3 rounded-lg border p-4"
    >
      <h4 className="font-medium">
        {row.employee_name} · {assignmentStatuses[row.status]}
      </h4>
      <p className="text-sm">
        投入 {row.allocation_percent}% · 预计完成：{row.due_date}
      </p>
      {canEdit && row.status === "active" && (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() => update.mutate({ status: "done" })}
            >
              标记完成
            </Button>
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() => update.mutate({ status: "cancelled" })}
            >
              取消分配
            </Button>
          </div>
          <form
            aria-label="修改完成日期"
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault()
              update.mutate({
                due_date: dueDate,
                ...commitmentBody(new FormData(event.currentTarget)),
              })
            }}
          >
            <fieldset disabled={update.isPending} className="space-y-3">
              <DateField
                label="调整后的完成日期"
                value={dueDate}
                onChange={(event) => setDueDate(event.target.value)}
              />
              <CommitmentFields
                dueDate={dueDate}
                committedEndDate={committedEndDate}
              />
              <Button type="submit">保存完成日期</Button>
            </fieldset>
          </form>
        </>
      )}
      <CapacityError error={update.error} />
    </article>
  )
}
