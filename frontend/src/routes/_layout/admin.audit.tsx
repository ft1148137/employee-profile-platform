import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useRef, useState } from "react"
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
import {
  SideDrawer,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
import { OrgState } from "@/components/Profile/OrgState"
import { Badge } from "@/components/ui/badge"
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
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/audit")({
  component: AuditPage,
  head: () => ({ meta: [{ title: "审计日志 - 员工画像平台" }] }),
})

function AuditPage() {
  const { org } = useOrg()
  return org ? <AuditList key={org.id} orgId={org.id} /> : <OrgState />
}

function auditTime(date: string) {
  const d = new Date(date)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function auditActor({ actor, actor_type }: AuditEventPublic) {
  if (!actor) return actorLabels[actor_type] ?? actor_type
  return actor.full_name
    ? `${actor.full_name}（${actor.username}）`
    : actor.username
}

function auditTarget({ target_label, target_type }: AuditEventPublic) {
  return target_label ?? targetLabels[target_type] ?? target_type
}

function AuditAction({ action }: { action: string }) {
  const tone =
    /[._](revoked|disabled|deleted|denied|failed|retracted|withdrawn|unlinked|unbound|disconnected)$/.test(
      action,
    )
      ? "border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-300"
      : /[._](succeeded|granted|enabled|approved|allowed|accepted|published|connected|linked|bound)$/.test(
            action,
          )
        ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        : /[._](updated|saved|reset|returned|changed|renamed|moved)$/.test(
              action,
            )
          ? "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "border-primary/25 bg-primary/10 text-primary"
  return (
    <Badge variant="outline" className={cn("py-0 leading-5", tone)}>
      {auditLabels[action] ?? action}
    </Badge>
  )
}

const columns: ColumnDef<DataTableFeatures, AuditEventPublic>[] = [
  {
    id: "time",
    header: "时间",
    cell: ({ row: { original: event } }) => (
      <time
        dateTime={event.created_at}
        className="whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground"
      >
        {auditTime(event.created_at)}
      </time>
    ),
  },
  {
    id: "actor",
    header: "操作人",
    cell: ({ row }) => auditActor(row.original),
  },
  {
    id: "action",
    header: "操作类型",
    cell: ({ row }) => <AuditAction action={row.original.action} />,
  },
  {
    id: "target",
    header: "对象",
    cell: ({ row }) => auditTarget(row.original),
  },
  {
    id: "summary",
    header: "摘要",
    cell: ({ row }) => {
      const summary = auditSummary(row.original)
      return (
        <span
          className="block max-w-xs truncate text-muted-foreground"
          title={summary}
        >
          {summary}
        </span>
      )
    },
  },
]

function AuditList({ orgId }: { orgId: string }) {
  const [selected, setSelected] = useState<AuditEventPublic | null>(null)
  const trigger = useRef<HTMLElement | null>(null)
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
  return (
    <WorkspacePage
      className="space-y-2 [&_tbody_tr[tabindex]>td]:h-11"
      onKeyDownCapture={(event) => {
        // Keep Enter from activating the close button after the drawer takes focus.
        if (
          event.key === "Enter" &&
          event.target instanceof HTMLTableRowElement
        )
          event.preventDefault()
      }}
    >
      <h1 className="sr-only">审计日志</h1>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm">
        <div className="flex items-center gap-2">
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
              size="sm"
              className="w-36"
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
        <div className="flex items-center gap-2">
          <Label htmlFor="audit-since">开始日期</Label>
          <Input
            id="audit-since"
            className="h-8 w-36 min-w-0 text-sm"
            type="date"
            value={since}
            onChange={(e) => {
              setSince(e.target.value)
              reset()
            }}
          />
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="audit-until">结束日期（含当天）</Label>
          <Input
            id="audit-until"
            className="h-8 w-36 min-w-0 text-sm"
            type="date"
            value={until}
            onChange={(e) => {
              setUntil(e.target.value)
              reset()
            }}
          />
        </div>
        <p className="ml-auto whitespace-nowrap text-xs tabular-nums text-muted-foreground">
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
        <DataTable
          columns={columns}
          data={events.data.data}
          rowCount={events.data.count}
          pagination={pagination}
          onPaginationChange={setPagination}
          pageSizeOptions={[50]}
          getRowId={(event) => event.id}
          onRowClick={(event) => {
            trigger.current =
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null
            setSelected(event)
          }}
        />
      )}
      <SideDrawer
        title="审计事件详情"
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null)
            requestAnimationFrame(() => trigger.current?.focus())
          }
        }}
      >
        {selected && <AuditDetails event={selected} />}
      </SideDrawer>
    </WorkspacePage>
  )
}

function AuditDetails({ event }: { event: AuditEventPublic }) {
  const metadata = {
    时间: (
      <time dateTime={event.created_at} className="font-mono tabular-nums">
        {auditTime(event.created_at)}
      </time>
    ),
    操作人: auditActor(event),
    操作类型: (
      <>
        <AuditAction action={event.action} />
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {event.action}
        </p>
      </>
    ),
    对象: auditTarget(event),
    "对象 ID": (
      <span className="font-mono text-xs">{event.target_id ?? "—"}</span>
    ),
  }
  return (
    <div className="space-y-4 text-sm">
      <dl className="space-y-2 rounded-lg border border-surface-border p-3">
        {Object.entries(metadata).map(([label, value]) => (
          <div
            key={label}
            className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-2"
          >
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="[overflow-wrap:anywhere]">{value}</dd>
          </div>
        ))}
      </dl>
      <section aria-labelledby="audit-payload">
        <h3 id="audit-payload" className="mb-2 font-semibold">
          结构化载荷
        </h3>
        <pre className="whitespace-pre-wrap rounded-lg bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100 [overflow-wrap:anywhere]">
          {JSON.stringify(event.details, null, 2)}
        </pre>
      </section>
    </div>
  )
}
