import { useMutation, useQuery } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useId, useState } from "react"
import {
  type AssignmentInput,
  type AssignmentUpdateInput,
  CapacityService,
  type DemandAssignment,
  type DemandPublic,
  type DepartedDemandAssignment,
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
import { isCapacityAccessDenied } from "./shared"

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
  const [employeeId, setEmployeeId] = useState("")
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
    onError: (error) => {
      if (
        error instanceof AxiosError &&
        error.response?.status === 409 &&
        error.response.data?.detail?.code === "employee_departed"
      ) {
        setEmployeeId("")
        void basis.refetch()
      }
    },
  })
  if (
    isCapacityAccessDenied(basis.error) ||
    isCapacityAccessDenied(query.error)
  )
    return (
      <section
        aria-label="部门内分配"
        className="space-y-4 rounded-xl border p-5"
      >
        <h3 className="text-lg font-semibold">部门内分配</h3>
        <p role="status">你现在没有权限查看这部分</p>
      </section>
    )
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
      {demand.proposal?.status !== "committed" && (
        <form
          aria-label="新增分配"
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            const data = new FormData(event.currentTarget)
            create.mutate({
              employee_id: String(data.get("employee_id")),
              allocation_percent: Number(data.get("allocation_percent")),
              start_date: String(data.get("start_date")),
              outside_basis: data.has("outside_basis"),
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
                value={employeeId}
                onChange={(event) => setEmployeeId(event.target.value)}
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
                              "employee_id" in row &&
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
              label="开始日期"
              name="start_date"
              min={demand.committed_start_date ?? undefined}
            />
            <DateField
              label="预计完成日期"
              name="due_date"
              max={demand.committed_end_date ?? undefined}
            />
            {basis.data?.others.some(
              (person) => person.employee_id === employeeId,
            ) && (
              <label
                htmlFor={`${id}-outside`}
                key={employeeId}
                className="flex items-center gap-2 text-sm sm:col-span-3"
              >
                <Input
                  id={`${id}-outside`}
                  type="checkbox"
                  name="outside_basis"
                  required
                  className="size-4"
                />
                该员工不在依据中，我确认按自己的判断安排
              </label>
            )}
            <Button type="submit">确认分配</Button>
          </fieldset>
          <CapacityError error={create.error} />
        </form>
      )}
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
          {query.data.data.map((row, index) =>
            !("id" in row) ? (
              <article
                key={`borrow-${index}`}
                className="space-y-2 rounded-lg border p-3"
              >
                <h4 className="font-medium">借调 {row.headcount} 人</h4>
                <p className="text-sm">
                  投入 {row.allocation_percent}% · {row.start_date} 至{" "}
                  {row.due_date} · {assignmentStatuses[row.status]}
                </p>
              </article>
            ) : (
              <AssignmentRow
                key={`${row.id}:${row.start_date}:${row.due_date}`}
                orgId={orgId}
                row={row}
                committedEndDate={demand.committed_end_date ?? null}
                committedStartDate={demand.committed_start_date ?? null}
                canEdit={managedDepartmentIds.includes(
                  demand.target_department_id,
                )}
              />
            ),
          )}
        </>
      )}
    </section>
  )
}

function AssignmentRow({
  orgId,
  row,
  committedEndDate,
  committedStartDate,
  canEdit,
}: {
  orgId: string
  row: DemandAssignment | DepartedDemandAssignment
  committedEndDate: string | null
  committedStartDate: string | null
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
        {row.employee_name} ·{" "}
        {"status" in row ? assignmentStatuses[row.status] : "进行中"}
      </h4>
      <p className="text-sm">
        投入 {row.allocation_percent}% ·{" "}
        {row.start_date ? `开始：${row.start_date}` : "开始日期未确认"} ·
        预计完成：{row.due_date}
      </p>
      {canEdit && (!("status" in row) || row.status === "active") && (
        <>
          <div className="flex flex-wrap gap-2">
            {"status" in row && (
              <Button
                variant="outline"
                disabled={update.isPending}
                onClick={() => update.mutate({ status: "done" })}
              >
                标记完成
              </Button>
            )}
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() => update.mutate({ status: "cancelled" })}
            >
              取消分配
            </Button>
          </div>
          {"status" in row && (
            <form
              aria-label="修改开始日期"
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                update.mutate({
                  start_date: String(
                    new FormData(event.currentTarget).get("start_date"),
                  ),
                })
              }}
            >
              <fieldset disabled={update.isPending} className="space-y-3">
                <DateField
                  label="调整后的开始日期"
                  name="start_date"
                  defaultValue={row.start_date ?? ""}
                  min={committedStartDate ?? undefined}
                  max={row.due_date}
                />
                <Button type="submit">保存开始日期</Button>
              </fieldset>
            </form>
          )}
          {"status" in row && (
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
                  min={row.start_date ?? undefined}
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
          )}
        </>
      )}
      <CapacityError error={update.error} />
    </article>
  )
}
