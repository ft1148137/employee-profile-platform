import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useState } from "react"
import { type AuditEventPublic, AuditService } from "@/client"
import {
  actorLabels,
  auditLabels,
  auditSummary,
  localMidnight,
  targetLabels,
} from "@/components/Admin/auditLabels"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { OrgState } from "@/components/Profile/OrgState"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/audit")({
  component: AuditPage,
  head: () => ({ meta: [{ title: "审计日志 - 员工画像平台" }] }),
})

function AuditPage() {
  const { org } = useOrg()
  return org ? <AuditList key={org.id} orgId={org.id} /> : <OrgState />
}

function AuditList({ orgId }: { orgId: string }) {
  const [action, setAction] = useState("all")
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 50 })
  const reset = () => setPagination((old) => ({ ...old, pageIndex: 0 }))
  const events = useQuery({
    queryKey: ["audit", orgId, action, since, until, pagination],
    queryFn: async () =>
      (
        await AuditService.readAuditEvents({
          path: { org_id: orgId },
          query: {
            action: action === "all" ? undefined : action,
            since: localMidnight(since),
            until: localMidnight(until, true),
            skip: pagination.pageIndex * 50,
            limit: 50,
          },
        })
      ).data,
    retry: false,
  })
  let error = ""
  if (events.error)
    handleError.call((message) => {
      error = message
    }, events.error)
  const columns: ColumnDef<DataTableFeatures, AuditEventPublic>[] = [
    {
      id: "time",
      header: "时间",
      cell: ({ row }) => {
        const d = new Date(row.original.created_at)
        const pad = (n: number) => String(n).padStart(2, "0")
        return (
          <span className="whitespace-nowrap tabular-nums">{`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`}</span>
        )
      },
    },
    {
      id: "actor",
      header: "操作人",
      cell: ({ row: { original: e } }) =>
        e.actor
          ? e.actor.full_name
            ? `${e.actor.full_name}（${e.actor.username}）`
            : e.actor.username
          : (actorLabels[e.actor_type] ?? e.actor_type),
    },
    {
      id: "action",
      header: "操作",
      cell: ({ row }) =>
        auditLabels[row.original.action] ?? row.original.action,
    },
    {
      id: "target",
      header: "对象",
      cell: ({ row: { original: e } }) =>
        e.target_label ?? targetLabels[e.target_type] ?? e.target_type,
    },
    {
      id: "summary",
      header: "摘要",
      cell: ({ row }) => auditSummary(row.original),
    },
  ]
  return (
    <div className="space-y-6">
      <div className="border-l-4 border-primary pl-4">
        <p className="mb-1 text-xs tracking-widest text-muted-foreground">
          管理 · 操作记录
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">审计日志</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          查看本企业的操作记录，最新记录显示在最前面。
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-4 rounded-xl border bg-muted/20 p-4">
        <div className="space-y-2">
          <Label htmlFor="audit-action">操作类型</Label>
          <Select
            value={action}
            onValueChange={(value) => {
              setAction(value)
              reset()
            }}
          >
            <SelectTrigger
              id="audit-action"
              aria-label="操作类型筛选"
              className="w-48"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {Object.entries({
                "account.": "账号",
                "role.": "角色",
                "setup.": "首次设置",
                "roster.": "员工名单",
                ...auditLabels,
              }).map(([key, label]) => (
                <SelectItem key={key} value={key}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="audit-since">开始日期</Label>
          <Input
            id="audit-since"
            type="date"
            value={since}
            onChange={(e) => {
              setSince(e.target.value)
              reset()
            }}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="audit-until">结束日期（含当天）</Label>
          <Input
            id="audit-until"
            type="date"
            value={until}
            onChange={(e) => {
              setUntil(e.target.value)
              reset()
            }}
          />
        </div>
        <p className="ml-auto pb-2 text-sm text-muted-foreground">
          共 {events.data?.count ?? "—"} 条记录
        </p>
      </div>
      {events.isPending ? (
        <p role="status">正在加载审计日志…</p>
      ) : events.isError ? (
        <div role="alert">
          {error}
          <Button variant="outline" onClick={() => events.refetch()}>
            重试
          </Button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <DataTable
            columns={columns}
            data={events.data.data}
            rowCount={events.data.count}
            pagination={pagination}
            onPaginationChange={setPagination}
            pageSizeOptions={[50]}
          />
        </div>
      )}
    </div>
  )
}
