import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { RosterService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { changeLabels } from "./RosterPreview"

export function ImportHistory({ orgId }: { orgId: string }) {
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 })
  const history = useQuery({
    queryKey: ["roster-history", orgId, pagination],
    queryFn: async () =>
      (
        await RosterService.readImports({
          path: { org_id: orgId },
          query: {
            skip: pagination.pageIndex * pagination.pageSize,
            limit: pagination.pageSize,
          },
        })
      ).data,
  })
  return (
    <section className="space-y-3" aria-label="导入历史">
      <h3 className="text-lg font-semibold">导入历史</h3>
      {history.isPending ? (
        <p role="status">正在加载导入历史…</p>
      ) : history.isError ? (
        <>
          <SetupError error={history.error} />
          <Button onClick={() => history.refetch()}>重试</Button>
        </>
      ) : (
        <DataTable
          data={history.data.data}
          pagination={pagination}
          onPaginationChange={setPagination}
          rowCount={history.data.count}
          columns={[
            {
              accessorKey: "created_at",
              header: "上传时间",
              cell: ({ row }) =>
                new Date(row.original.created_at).toLocaleString("zh-CN"),
            },
            { accessorKey: "filename", header: "文件" },
            {
              id: "uploader",
              header: "上传人",
              cell: ({ row }) =>
                row.original.uploaded_by_full_name ??
                row.original.uploaded_by_username,
            },
            {
              accessorKey: "status",
              header: "状态",
              cell: ({ row }) =>
                ({ previewed: "已预览", applied: "已导入", stale: "已过期" })[
                  row.original.status
                ],
            },
            {
              id: "summary",
              header: "变更汇总",
              cell: ({ row }) =>
                Object.entries(changeLabels)
                  .map(
                    ([key, label]) =>
                      `${label} ${row.original.summary[key] ?? 0}`,
                  )
                  .join("；"),
            },
          ]}
        />
      )}
    </section>
  )
}
