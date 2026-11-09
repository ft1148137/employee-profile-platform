import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"
import { AccountsService, OrgsService, RolesService } from "@/client"
import { AccountActions } from "@/components/Admin/AccountTable"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { ConfirmActionDialog } from "@/components/Admin/ConfirmActionDialog"
import { Disputes } from "@/components/Admin/Disputes"
import { departmentOptions } from "@/components/Admin/departments"
import {
  accountLabels,
  employmentLabels,
  roleLabels,
} from "@/components/Admin/labels"
import { ResetPasswordDialog } from "@/components/Admin/ResetPasswordDialog"
import { RoleGrantDialog } from "@/components/Admin/RoleGrantDialog"
import { IdentityTable } from "@/components/Identities/IdentityTable"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/people/$employeeId")({
  component: Page,
  head: () => ({ meta: [{ title: "人员详情 - 员工画像平台" }] }),
})

function Page() {
  const { org, roles } = useOrg()
  const { employeeId } = Route.useParams()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <Person
      key={`${org.id}:${employeeId}`}
      orgId={org.id}
      employeeId={employeeId}
    />
  )
}

function Person({ orgId, employeeId }: { orgId: string; employeeId: string }) {
  const client = useQueryClient()
  const [resetPassword, setResetPassword] = useState(false)
  const person = useQuery({
    queryKey: ["accounts", orgId, "person", employeeId],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { employee_id: employeeId },
        })
      ).data,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const refresh = () => {
    for (const queryKey of [
      ["accounts", orgId],
      ["role-grants", orgId],
      ["memberships"],
      ["change-history", orgId],
    ])
      client.invalidateQueries({ queryKey })
  }
  if (person.isPending || departments.isPending) return <ProfileLoading />
  if (person.isError || departments.isError)
    return (
      <ProfileError
        retry={() => {
          person.refetch()
          departments.refetch()
        }}
      />
    )
  const employee = person.data.data[0]
  if (!employee) return <p role="alert">未找到该员工或你没有权限查看</p>
  const options = departmentOptions(departments.data.data)
  const fields = [
    ["工号", employee.employee_code],
    ["部门", options.find((d) => d.id === employee.department?.id)?.name],
    ["职位", employee.title],
    ["邮箱", employee.work_email],
    ["在职状态", employmentLabels[employee.employment_status]],
    ["账号状态", accountLabels[employee.account?.status ?? "none"]],
  ]
  return (
    <div className="space-y-6">
      <Link
        className="text-sm text-muted-foreground underline"
        to="/admin/accounts"
      >
        返回人员列表
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4 border-l-4 border-primary pl-4">
        <div>
          <p className="mb-2 text-xs tracking-widest text-muted-foreground">
            管理 · 人员详情
          </p>
          <h1 className="text-3xl font-semibold">{employee.display_name}</h1>
        </div>
        <Link
          className="text-sm text-primary underline underline-offset-4"
          to="/employees/$employeeId"
          params={{ employeeId }}
        >
          查看员工画像
        </Link>
      </header>
      <section aria-label="员工信息" className="rounded-xl border p-5">
        <dl className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {fields.map(([name, value]) => (
            <div key={name}>
              <dt className="text-xs text-muted-foreground">{name}</dt>
              <dd className="mt-1 break-words text-sm font-medium">
                {value || "未填写"}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section
        aria-label="人员操作"
        className="space-y-4 rounded-xl border p-5"
      >
        <h2 className="text-lg font-semibold">账号与角色</h2>
        <div className="flex flex-wrap items-center gap-3">
          <AccountActions
            employee={employee}
            onResetPassword={() => setResetPassword(true)}
          />
          {employee.account &&
            employee.account.status !== "disabled" &&
            employee.employment_status === "active" && (
              <RoleGrantDialog
                orgId={orgId}
                employeeId={employeeId}
                grants={employee.roles}
                departments={options}
                onSuccess={refresh}
              />
            )}
        </div>
        {employee.roles.length === 0 && (
          <p className="text-sm text-muted-foreground">暂无管理角色</p>
        )}
        {employee.roles.map((grant) => (
          <div
            key={grant.id}
            className="flex flex-wrap items-center gap-3 text-sm"
          >
            <span>
              {roleLabels[grant.role]}
              {grant.department
                ? ` · ${options.find((d) => d.id === grant.department_id)?.name ?? grant.department.name}`
                : ""}
            </span>
            <ConfirmActionDialog
              title="撤销角色"
              description={`确认撤销${employee.display_name}的${roleLabels[grant.role]}角色？`}
              action={() =>
                RolesService.revokeRole({
                  path: { org_id: orgId, grant_id: grant.id },
                })
              }
              onSuccess={refresh}
            />
          </div>
        ))}
      </section>
      {resetPassword && employee.account && (
        <ResetPasswordDialog
          orgId={orgId}
          userId={employee.account.user_id}
          name={employee.display_name}
          open
          onClose={() => {
            setResetPassword(false)
            refresh()
          }}
        />
      )}
      <section
        aria-label="Jira 账号匹配"
        className="space-y-4 rounded-xl border p-5"
      >
        <h2 className="text-lg font-semibold">Jira 账号匹配</h2>
        <IdentityTable orgId={orgId} employeeId={employeeId} />
      </section>
      <Disputes orgId={orgId} employeeId={employeeId} />
      <ChangeHistory employeeId={employeeId} />
    </div>
  )
}
