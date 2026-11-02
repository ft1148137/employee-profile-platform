import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AccountsService, OrgsService, RolesService } from "@/client"
import { departmentOptions } from "@/components/Admin/departments"
import { IssueAccountsDialog } from "@/components/Admin/IssueAccountsDialog"
import { Button } from "@/components/ui/button"
import { SetupError } from "../SetupError"
import type { StepProps } from "."

export function AccountsStep({ orgId }: StepProps) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<Record<string, string>>({})
  const count = useQuery({
    queryKey: ["accounts", orgId, "unissued-count"],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { status: "none", employment_status: "active", limit: 1 },
        })
      ).data.count,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const roles = useQuery({
    queryKey: ["roles", orgId],
    queryFn: async () =>
      (await RolesService.readRoles({ path: { org_id: orgId } })).data,
  })
  const employees = useQuery({
    queryKey: ["accounts", orgId, "manager-candidates"],
    queryFn: async () => {
      const first = (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { employment_status: "active", limit: 100 },
        })
      ).data
      const result = [...first.data]
      for (let skip = 100; skip < first.count; skip += 100) {
        result.push(
          ...(
            await AccountsService.readAccounts({
              path: { org_id: orgId },
              query: { employment_status: "active", skip, limit: 100 },
            })
          ).data.data,
        )
      }
      return result
    },
  })
  const save = useMutation({
    mutationFn: ({
      departmentId,
      userId,
    }: {
      departmentId: string
      userId: string
    }) =>
      RolesService.grantRole({
        path: { org_id: orgId },
        body: {
          role: "dept_manager",
          department_id: departmentId,
          user_id: userId,
        },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["roles", orgId] }),
  })
  const error =
    count.error ?? departments.error ?? employees.error ?? roles.error
  return (
    <div className="space-y-5">
      <p role="status">
        {count.isPending
          ? "正在统计员工…"
          : count.isError
            ? "无法统计员工人数"
            : `还有 ${count.data} 名在职员工没有账号`}
      </p>
      <IssueAccountsDialog
        orgId={orgId}
        onIssued={() => {
          client.invalidateQueries({ queryKey: ["accounts", orgId] })
          client.invalidateQueries({ queryKey: ["setup", orgId] })
        }}
      />
      <section className="space-y-4 border-t pt-5" aria-label="指定部门经理">
        <h3 className="font-semibold">
          指定部门经理（可选，以后也可以在「角色」页修改）
        </h3>
        <SetupError error={error} />
        {error && (
          <Button
            variant="outline"
            onClick={() => {
              count.refetch()
              departments.refetch()
              employees.refetch()
              roles.refetch()
            }}
          >
            重试
          </Button>
        )}
        {(departments.isPending || employees.isPending || roles.isPending) && (
          <p role="status">正在加载部门和账号…</p>
        )}
        {!error &&
          departments.isSuccess &&
          employees.isSuccess &&
          roles.isSuccess &&
          departmentOptions(departments.data.data).map((department) => {
            const candidates = employees.data.filter(
              (employee) =>
                employee.department?.id === department.id &&
                employee.account &&
                employee.account.status !== "disabled",
            )
            const managers = roles.data.filter(
              (role) =>
                role.role === "dept_manager" &&
                role.department_id === department.id,
            )
            return (
              <div
                key={department.id}
                className="grid items-center gap-3 rounded-lg border p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
              >
                <div>
                  <p className="break-words font-medium">{department.name}</p>
                  <p className="text-xs text-muted-foreground">
                    当前经理：
                    {managers
                      .map((role) => role.display_name ?? role.username)
                      .join("、") || "未指定"}
                  </p>
                </div>
                <select
                  aria-label={`${department.name}经理`}
                  value={selected[department.id] ?? ""}
                  disabled={save.isPending || !candidates.length}
                  className="h-9 w-full min-w-0 rounded-md border bg-background px-2 text-sm"
                  onChange={(event) => {
                    setSelected({
                      ...selected,
                      [department.id]: event.target.value,
                    })
                    save.reset()
                  }}
                >
                  <option value="">
                    {candidates.length
                      ? "选择本部门员工"
                      : "本部门暂无可选账号"}
                  </option>
                  {candidates.map((employee) => (
                    <option key={employee.id} value={employee.account?.user_id}>
                      {employee.display_name}（{employee.employee_code}）
                    </option>
                  ))}
                </select>
                <Button
                  variant="outline"
                  disabled={save.isPending || !selected[department.id]}
                  onClick={() =>
                    save.mutate({
                      departmentId: department.id,
                      userId: selected[department.id],
                    })
                  }
                >
                  保存经理
                </Button>
              </div>
            )
          })}
        <SetupError error={save.error} />
        {save.isSuccess && <p role="status">部门经理已保存。</p>}
      </section>
    </div>
  )
}
