import { useQuery, useQueryClient } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useState } from "react"
import {
  AccountsService,
  type EmployeeSummary,
  OrgsService,
  RolesService,
} from "@/client"
import { AccountActions } from "@/components/Admin/AccountTable"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { ConfirmActionDialog } from "@/components/Admin/ConfirmActionDialog"
import { Disputes } from "@/components/Admin/Disputes"
import { departmentOptions } from "@/components/Admin/departments"
import { IssueAccountsDialog } from "@/components/Admin/IssueAccountsDialog"
import {
  accountLabels,
  employmentLabels,
  roleLabels,
} from "@/components/Admin/labels"
import { PersonnelDialog } from "@/components/Admin/PersonnelDialog"
import { ResetPasswordDialog } from "@/components/Admin/ResetPasswordDialog"
import { RoleGrantDialog } from "@/components/Admin/RoleGrantDialog"
import { IdentityTable } from "@/components/Identities/IdentityTable"
import { EmployeeSummaryContent } from "@/components/Profile/EmployeeSummary"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import {
  employeeFieldLabels,
  tenureLabel,
} from "@/components/Roster/EmployeeFields"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOrg } from "@/hooks/useOrg"

export function EmployeeDetailTabs({
  employee: summary,
  tab,
  onTabChange,
  children,
}: {
  employee: EmployeeSummary
  tab?: string
  onTabChange: (tab: string) => void
  children: ReactNode
}) {
  const { org, roles } = useOrg()
  const orgId = org!.id
  const employeeId = summary.id
  const isAdmin = roles.includes("org_admin")
  const client = useQueryClient()
  const [resetPassword, setResetPassword] = useState(false)
  const person = useQuery({
    queryKey: ["accounts", orgId, "person", employeeId],
    enabled: isAdmin,
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
    enabled: isAdmin,
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
  const employee = person.data?.data[0]
  const options = departmentOptions(departments.data?.data ?? [])
  const tabs = [
    ["profile", "画像"],
    ["fields", "档案"],
    ...(isAdmin
      ? [
          ["account", "账号与角色"],
          ["identities", "Jira 账号"],
          ["appeals", "争议"],
        ]
      : []),
  ]
  const selected = tabs.some(([value]) => value === tab) ? tab : "profile"
  const fields = [
    ...(org?.employee_fields ?? []).map((field) => [
      field === "hire_date" ? "入职年限" : employeeFieldLabels[field],
      field === "hire_date" && summary.hire_date
        ? tenureLabel(summary.hire_date)
        : summary[field],
    ]),
    ...(employee
      ? [
          ["工号", employee.employee_code],
          ["邮箱", employee.work_email],
        ]
      : []),
    [
      "部门",
      options.find((d) => d.id === summary.department?.id)?.name ??
        summary.department?.name,
    ],
    ["职位", summary.title],
    ["在职状态", employmentLabels[summary.employment_status]],
    ["直属上级", summary.manager?.display_name],
  ]
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b pb-6">
        <div className="flex items-center gap-4">
          <Avatar className="size-16 text-2xl">
            <AvatarFallback>{summary.display_name.slice(0, 1)}</AvatarFallback>
          </Avatar>
          <div>
            <h1 className="text-3xl font-semibold">{summary.display_name}</h1>
            <p className="mt-2 text-muted-foreground">
              {summary.title ?? "未填写职位"} ·{" "}
              {summary.department?.name ?? "未分配部门"}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              直属上级：{summary.manager?.display_name ?? "暂无上级"}
            </p>
          </div>
          <Badge variant="secondary">
            {employmentLabels[summary.employment_status]}
          </Badge>
        </div>
        {isAdmin &&
          employee?.employment_status === "active" &&
          departments.isSuccess && (
            <section aria-label="人员操作" className="flex gap-3">
              <PersonnelDialog
                orgId={orgId}
                employee={employee}
                department={
                  options.find((d) => d.id === summary.department?.id)?.name
                }
              />
              <PersonnelDialog orgId={orgId} employee={employee} offboard />
            </section>
          )}
      </header>
      {isAdmin && (person.isError || departments.isError) && (
        <ProfileError
          retry={() => {
            person.refetch()
            departments.refetch()
          }}
        />
      )}
      <Tabs value={selected} onValueChange={onTabChange} className="gap-6">
        <TabsList
          aria-label="员工详情"
          className="h-auto flex-wrap justify-start"
        >
          {tabs.map(([value, title]) => (
            <TabsTrigger key={value} value={value}>
              {title}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="profile" className="space-y-6">
          <EmployeeSummaryContent employee={summary} showHeading={false} />
          {children}
        </TabsContent>
        <TabsContent value="fields" className="space-y-6">
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
          {isAdmin && <ChangeHistory employeeId={employeeId} />}
        </TabsContent>
        {isAdmin && (
          <TabsContent value="account" className="space-y-6">
            {person.isPending ? (
              <ProfileLoading />
            ) : (
              employee && (
                <section
                  aria-label="账号与角色"
                  className="space-y-4 rounded-xl border p-5"
                >
                  <h2 className="text-lg font-semibold">账号与角色</h2>
                  <p>
                    邮箱：
                    {employee.account?.username ??
                      employee.work_email ??
                      "未填写"}{" "}
                    · {accountLabels[employee.account?.status ?? "none"]}
                  </p>
                  <div className="flex flex-wrap items-center gap-3">
                    {employee.employment_status === "active" &&
                      (!employee.account ||
                        employee.account.status === "pending_first_login") && (
                        <IssueAccountsDialog
                          orgId={orgId}
                          employees={[
                            {
                              id: employee.id,
                              employee_code: employee.employee_code,
                              display_name: employee.display_name,
                              status: employee.account?.status ?? "none",
                            },
                          ]}
                          onIssued={refresh}
                        />
                      )}
                    <AccountActions
                      employee={employee}
                      onResetPassword={() => setResetPassword(true)}
                    />
                    {employee.account &&
                      employee.account.status !== "disabled" &&
                      departments.isSuccess &&
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
                    <p className="text-sm text-muted-foreground">
                      暂无管理角色
                    </p>
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
              )
            )}
            <ChangeHistory employeeId={employeeId} />
            {resetPassword && employee?.account && (
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
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="identities">
            <h2 className="mb-4 text-lg font-semibold">Jira 账号匹配</h2>
            <p className="mb-4 text-sm text-muted-foreground">
              解除后不会再次自动关联，仍可手动选择账号。
            </p>
            <IdentityTable orgId={orgId} employeeId={employeeId} />
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="appeals">
            <Disputes orgId={orgId} employeeId={employeeId} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
