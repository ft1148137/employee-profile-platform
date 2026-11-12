import { useQuery } from "@tanstack/react-query"
import type { CellContext } from "@tanstack/react-table"
import { useCallback, useState } from "react"
import { AccountsService, type UnlinkedAccount } from "@/client"
import { AccountMenu } from "@/components/Admin/AccountTable"
import { accountLabels, roleLabels } from "@/components/Admin/labels"
import { ResetPasswordDialog } from "@/components/Admin/ResetPasswordDialog"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function UnlinkedAccounts({ orgId }: { orgId: string }) {
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 25 })
  const [target, setTarget] = useState<UnlinkedAccount | null>(null)
  const actions = useCallback(
    ({ row }: CellContext<DataTableFeatures, UnlinkedAccount>) => (
      <AccountMenu
        employee={row.original}
        onResetPassword={() => setTarget(row.original)}
      />
    ),
    [],
  )
  const query = useQuery({
    queryKey: ["accounts", orgId, "unlinked", pagination],
    queryFn: async () =>
      (
        await AccountsService.readUnlinkedAccounts({
          path: { org_id: orgId },
          query: {
            skip: pagination.pageIndex * pagination.pageSize,
            limit: pagination.pageSize,
          },
        })
      ).data,
  })
  return (
    <div className="space-y-4">
      <p>这些账号没有员工身份，角色仅供查看。</p>
      {query.isPending ? (
        <p role="status">正在加载账号…</p>
      ) : query.isError ? (
        <>
          <SetupError error={query.error} />
          <Button onClick={() => query.refetch()}>重试</Button>
        </>
      ) : (
        <DataTable
          data={query.data.data}
          getRowId={(row) => row.account.user_id}
          pagination={pagination}
          onPaginationChange={setPagination}
          rowCount={query.data.count}
          columns={[
            {
              accessorKey: "display_name",
              header: "姓名",
              cell: ({ row }) => row.original.display_name ?? "—",
            },
            {
              id: "username",
              header: "登录名",
              cell: ({ row }) => row.original.account.username,
            },
            {
              id: "status",
              header: "账号状态",
              cell: ({ row }) => accountLabels[row.original.account.status],
            },
            {
              id: "roles",
              header: "角色",
              cell: ({ row }) =>
                row.original.roles
                  .map(
                    (role) =>
                      `${roleLabels[role.role]}${role.department ? `（${role.department.name}）` : ""}`,
                  )
                  .join("、") || "—",
            },
            {
              id: "login",
              header: "最近登录",
              cell: ({ row }) =>
                row.original.account.last_login_at
                  ? new Date(row.original.account.last_login_at).toLocaleString(
                      "zh-CN",
                    )
                  : "—",
            },
            {
              id: "actions",
              header: "操作",
              cell: actions,
            },
          ]}
        />
      )}
      {target && (
        <ResetPasswordDialog
          orgId={orgId}
          userId={target.account.user_id}
          name={target.display_name ?? target.account.username}
          open
          onClose={() => {
            setTarget(null)
            query.refetch()
          }}
        />
      )}
    </div>
  )
}
