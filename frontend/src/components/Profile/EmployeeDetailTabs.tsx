import { useQuery, useQueryClient } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useState } from "react"
import {
  AccountsService,
  type EmployeeSummary,
  OrgsService,
  RolesService,
  RosterService,
} from "@/client"
import { AccountActions } from "@/components/Admin/AccountTable"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { ConfirmActionDialog } from "@/components/Admin/ConfirmActionDialog"
import { Disputes, useDisputes } from "@/components/Admin/Disputes"
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
import { ProfileVisibility } from "@/components/Profile/ProfileVisibility"
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
  evidence,
}: {
  employee: EmployeeSummary
  tab?: string
  onTabChange: (tab: string) => void
  children: ReactNode
  evidence: ReactNode
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
  const previousManager = useQuery({
    queryKey: ["previous-manager", orgId, employeeId],
    enabled:
      isAdmin && summary.employment_status === "active" && !summary.manager,
    queryFn: async () =>
      (
        await RosterService.readPreviousManager({
          path: { org_id: orgId, employee_id: employeeId },
        })
      ).data,
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
  const disputes = useDisputes(isAdmin ? orgId : undefined, employeeId)
  const pendingDisputes = disputes.data?.length ?? 0
  const showAppeals = tab === "appeals"
  const selected =
    tab === "account" || tab === "fields" || tab === "identities"
      ? "account"
      : isAdmin && (tab === "evidence" || tab === "versions")
        ? "evidence"
        : "capabilities"
  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <Avatar className="size-9 shrink-0">
            <AvatarFallback className="bg-primary/10 font-semibold text-primary">
              {summary.display_name.slice(0, 1)}
            </AvatarFallback>
          </Avatar>
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <h1 className="font-semibold [overflow-wrap:anywhere]">
              {summary.display_name}
            </h1>
            <p className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">
              工号 {summary.employee_code ?? "未填写"}
            </p>
            <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
              {summary.title ?? "未填写职位"} ·{" "}
              {summary.department?.name ?? "未分配部门"}
            </p>
            {isAdmin && (
              <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
                直属上级：{summary.manager?.display_name ?? "暂无上级"}
              </p>
            )}
            <Badge
              variant="secondary"
              className={
                summary.employment_status === "active"
                  ? "rounded-full bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200"
                  : "rounded-full"
              }
            >
              {summary.employment_status === "inactive"
                ? "已离职 · 归档只读"
                : employmentLabels[summary.employment_status]}
            </Badge>
            <ProfileVisibility tooltip />
          </div>
        </div>
        {isAdmin &&
          employee?.employment_status === "active" &&
          departments.isSuccess && (
            <section
              aria-label="人员操作"
              className="ml-auto flex flex-wrap gap-2 [&_[data-slot=dialog-trigger]]:h-8"
            >
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
      {summary.employment_status === "active" &&
        !summary.manager &&
        previousManager.data?.id && (
          <section
            aria-label="重新设置直属上级"
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm"
          >
            <p>直属上级未设置</p>
            {previousManager.data.is_active === false ? (
              <p>
                原直属上级 {previousManager.data.display_name}{" "}
                已离职，请重新指定。
              </p>
            ) : previousManager.data.can_restore &&
              employee &&
              departments.isSuccess ? (
              <PersonnelDialog
                orgId={orgId}
                employee={employee}
                department={
                  options.find((d) => d.id === summary.department?.id)?.name
                }
                initialManager={previousManager.data}
              />
            ) : (
              <p>请重新设置直属上级</p>
            )}
          </section>
        )}
      {isAdmin &&
        (person.isError || departments.isError || previousManager.isError) && (
          <ProfileError
            retry={() => {
              person.refetch()
              departments.refetch()
              previousManager.refetch()
            }}
          />
        )}
      {isAdmin && (pendingDisputes > 0 || showAppeals) && (
        <details
          key={String(showAppeals)}
          open={showAppeals}
          className="rounded-xl border border-amber-500/40 bg-amber-500/10"
        >
          <summary className="cursor-pointer px-4 py-3 font-medium">
            争议处理{disputes.isSuccess && ` · ${pendingDisputes} 项待处理`}
          </summary>
          <div className="border-t border-amber-500/40 bg-background p-4">
            <Disputes orgId={orgId} employeeId={employeeId} />
          </div>
        </details>
      )}
      <Tabs
        value={selected}
        onValueChange={onTabChange}
        className="min-w-0 gap-3"
      >
        <TabsList
          aria-label="员工详情"
          className="h-auto max-w-full flex-wrap justify-start"
        >
          <TabsTrigger value="capabilities">能力画像</TabsTrigger>
          {isAdmin && <TabsTrigger value="evidence">证据与历史</TabsTrigger>}
          <TabsTrigger value="account">
            {isAdmin ? "档案与账号" : "档案"}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="capabilities" className="space-y-3">
          {children}
          {isAdmin && (
            <div className="[&_dl]:gap-3 [&_dl]:rounded-lg [&_dl]:border-surface-border [&_dl]:bg-surface [&_dl]:p-3 [&_dt]:mb-1 [&_dt]:text-xs [&_dd]:text-sm">
              <EmployeeSummaryContent employee={summary} showHeading={false} />
            </div>
          )}
        </TabsContent>
        {isAdmin && (
          <TabsContent value="evidence" className="space-y-6">
            {evidence}
          </TabsContent>
        )}
        <TabsContent value="account" className="space-y-6">
          <EmployeeFields
            summary={summary}
            workEmail={employee?.work_email}
            departmentName={
              options.find((d) => d.id === summary.department?.id)?.name
            }
          />
          {isAdmin && (
            <>
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
                          employee.account.status ===
                            "pending_first_login") && (
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
              <section
                className="rounded-xl border p-5"
                aria-label="关联 Jira 账号"
              >
                <h2 className="mb-4 text-lg font-semibold">关联 Jira 账号</h2>
                <p className="mb-4 text-sm text-muted-foreground">
                  解除后不会再次自动关联，仍可手动选择账号。
                </p>
                <IdentityTable orgId={orgId} employeeId={employeeId} />
              </section>
              <ChangeHistory employeeId={employeeId} />
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function EmployeeFields({
  summary,
  workEmail,
  departmentName,
}: {
  summary: EmployeeSummary
  workEmail?: string | null
  departmentName?: string
}) {
  const { org } = useOrg()
  const fields = [
    ...(org?.employee_fields ?? []).map((field) => [
      field === "hire_date" ? "入职年限" : employeeFieldLabels[field],
      field === "hire_date" && summary.hire_date
        ? tenureLabel(summary.hire_date)
        : summary[field],
    ]),
    ["工号", summary.employee_code],
    ...(workEmail !== undefined ? [["邮箱", workEmail]] : []),
    ["部门", departmentName ?? summary.department?.name],
    ["职位", summary.title],
    ["在职状态", employmentLabels[summary.employment_status]],
    ["直属上级", summary.manager?.display_name],
  ]

  return (
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
  )
}
