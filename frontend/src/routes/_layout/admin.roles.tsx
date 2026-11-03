import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { OrgsService, RolesService } from "@/client"
import { ConfirmActionDialog } from "@/components/Admin/ConfirmActionDialog"
import { departmentOptions } from "@/components/Admin/departments"
import { roleLabels } from "@/components/Admin/labels"
import { RoleGrantDialog } from "@/components/Admin/RoleGrantDialog"
import { DataTable } from "@/components/Common/DataTable"
import { OrgState } from "@/components/Profile/OrgState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/roles")({
  component: RolesPage,
  head: () => ({ meta: [{ title: "角色 - 员工画像平台" }] }),
})

function RolesPage() {
  const { org } = useOrg()
  return org ? <RolesList key={org.id} orgId={org.id} /> : <OrgState />
}

function RolesList({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const { roles } = useOrg()
  const grants = useQuery({
    queryKey: ["role-grants", orgId],
    queryFn: async () =>
      (await RolesService.readRoles({ path: { org_id: orgId } })).data,
    retry: false,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const options = departmentOptions(departments.data?.data ?? [])
  const paths = new Map(
    options.map((department) => [department.id, department.name]),
  )
  function refresh() {
    for (const queryKey of [
      ["role-grants", orgId],
      ["accounts", orgId],
      ["memberships"],
    ])
      client.invalidateQueries({ queryKey })
  }
  let error = ""
  if (grants.error)
    handleError.call((message) => {
      error = message
    }, grants.error)
  const rows = [...(grants.data ?? [])].sort((a, b) => {
    if (a.role !== b.role) return a.role === "org_admin" ? -1 : 1
    return (paths.get(a.department_id ?? "") ?? "").localeCompare(
      paths.get(b.department_id ?? "") ?? "",
      "zh-CN",
    )
  })
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 border-l-4 border-primary pl-4">
        <div>
          <p className="mb-1 text-xs tracking-widest text-muted-foreground">
            管理 · 权限分工
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">角色</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            管理企业管理员与部门经理的角色授予。
          </p>
        </div>
        {roles.includes("org_admin") &&
          grants.isSuccess &&
          departments.isSuccess && (
            <RoleGrantDialog
              orgId={orgId}
              grants={grants.data}
              departments={options}
              onSuccess={refresh}
            />
          )}
      </div>
      {departments.isError && (
        <p role="alert">
          无法加载部门路径。
          <Button variant="outline" onClick={() => departments.refetch()}>
            重试
          </Button>
        </p>
      )}
      {grants.isPending || departments.isPending ? (
        <p role="status">正在加载角色…</p>
      ) : grants.isError ? (
        <p role="alert">
          {error}
          <Button variant="outline" onClick={() => grants.refetch()}>
            重试
          </Button>
        </p>
      ) : (
        <DataTable
          data={rows}
          getRowId={(grant) => grant.id}
          columns={[
            {
              accessorKey: "display_name",
              header: "姓名",
              cell: ({ row }) => row.original.display_name ?? "—",
            },
            { accessorKey: "username", header: "登录名" },
            {
              id: "role",
              header: "角色",
              cell: ({ row }) => roleLabels[row.original.role],
            },
            {
              id: "department",
              header: "部门",
              cell: ({ row }) =>
                paths.get(row.original.department_id ?? "") ?? "—",
            },
            {
              id: "actions",
              header: "操作",
              cell: ({ row: { original: grant } }) => (
                <ConfirmActionDialog
                  title="撤销"
                  description={`撤销后，${grant.display_name ?? grant.username} 将不再是 ${grant.role === "dept_manager" ? `${paths.get(grant.department_id ?? "") ?? grant.department?.name} 的` : ""}${roleLabels[grant.role]}。`}
                  action={() =>
                    RolesService.revokeRole({
                      path: { org_id: orgId, grant_id: grant.id },
                    })
                  }
                  onSuccess={refresh}
                />
              ),
            },
          ]}
        />
      )}
    </div>
  )
}
