import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { useRef, useState } from "react"
import { CapacityService, type WorkloadPublic } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { Unavailability } from "./MyWorkload"
import { addWeeks, weekLabel } from "./shared"
import {
  TeamWorkloadGantt,
  WorkloadDetails,
  WorkloadLegend,
  type WorkloadSelection,
} from "./TeamWorkloadGantt"

type WorkloadDialog =
  | { mode: "details"; selection: WorkloadSelection }
  | { mode: "edit" | "dates"; row: WorkloadPublic; week: string }

export function TeamWorkload({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const { employeeId, managedDepartmentIds, roles } = useOrg()
  const isAdmin = roles.includes("org_admin")
  const [department, setDepartment] = useState("all")
  const [offset, setOffset] = useState(0)
  const [dialog, setDialog] = useState<WorkloadDialog | null>(null)
  const dialogOpener = useRef<HTMLElement | null>(null)
  const list = async (from_week?: string) =>
    (
      await CapacityService.listWorkloads({
        path: { org_id: orgId },
        query: { from_week, weeks: 8 },
      })
    ).data.data
  const current = useQuery({
    queryKey: ["capacity", orgId, "team"],
    queryFn: () => list(),
  })
  const currentWeek = current.data?.[0]?.weeks[0]?.week_start
  const fromWeek =
    currentWeek && offset ? addWeeks(currentWeek, offset) : undefined
  const shifted = useQuery({
    queryKey: ["capacity", orgId, "team", fromWeek],
    queryFn: () => list(fromWeek),
    enabled: !!fromWeek,
  })
  const visible = (row: WorkloadPublic) =>
    department === "all" || row.department_id === department
  const canEdit = (row: WorkloadPublic) =>
    !isAdmin &&
    row.employee_id !== employeeId &&
    !!row.department_id &&
    managedDepartmentIds.includes(row.department_id)
  const eligible = (current.data ?? []).filter(
    (row) => visible(row) && canEdit(row),
  )
  const reusable = eligible.flatMap((row) =>
    row.last_confirmed
      ? [
          {
            employee_id: row.employee_id,
            other_percent: row.last_confirmed.percent,
          },
        ]
      : [],
  )
  const confirm = useMutation({
    mutationFn: () =>
      CapacityService.setCapacityWeeks({
        path: { org_id: orgId },
        body: {
          items: reusable,
          start_week: currentWeek!,
          end_week: addWeeks(currentWeek!, 3),
        },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
    },
  })
  if (current.isPending) return <ProfileLoading />
  if (current.isError) return <ProfileError retry={() => current.refetch()} />
  const chart = fromWeek ? shifted : current
  const departments = new Map(
    current.data.flatMap((row) =>
      row.department_id && row.department_name
        ? [[row.department_id, row.department_name] as const]
        : [],
    ),
  )
  const openDialog = (next: WorkloadDialog) => {
    if (!dialog)
      dialogOpener.current = document.activeElement as HTMLElement | null
    setDialog(next)
  }
  const actions = (
    row: WorkloadPublic,
    week = row.weeks[0].week_start,
    inDetails = false,
  ) => (
    <div className="flex flex-wrap gap-1.5 pt-1">
      {canEdit(row) && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => openDialog({ mode: "edit", row, week })}
        >
          {inDetails ? "设置周投入" : "设置"}
        </Button>
      )}
      {(isAdmin || canEdit(row)) && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => openDialog({ mode: "dates", row, week })}
        >
          {inDetails && canEdit(row) ? "登记不可用日期" : "不可用日期"}
        </Button>
      )}
    </div>
  )
  return (
    <div className="min-w-0 space-y-2 md:space-y-4">
      <p className="text-sm text-muted-foreground">
        展示未来 8 周的投入参考，实际承接以部门经理答复为准。
      </p>
      {isAdmin && (
        <p className="text-sm text-muted-foreground">
          管理员仅可查看，调整需由对应部门经理操作。
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Label htmlFor="capacity-department">部门筛选</Label>
          <Select value={department} onValueChange={setDepartment}>
            <SelectTrigger id="capacity-department" className="max-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部部门</SelectItem>
              {Array.from(departments, ([id, name]) => (
                <SelectItem key={id} value={id}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {currentWeek && (
          <fieldset
            className="flex min-w-0 flex-wrap items-center gap-1"
            aria-label="时间窗口"
          >
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOffset((weeks) => weeks - 4)}
            >
              <ChevronLeft aria-hidden="true" />
              上一期
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!offset}
              onClick={() => setOffset(0)}
            >
              回到本周
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOffset((weeks) => weeks + 4)}
            >
              下一期
              <ChevronRight aria-hidden="true" />
            </Button>
            <span
              className="sr-only ml-1 text-xs text-muted-foreground sm:not-sr-only"
              aria-live="polite"
            >
              {weekLabel(fromWeek ?? currentWeek)}起
            </span>
          </fieldset>
        )}
      </div>
      {!isAdmin && !!managedDepartmentIds.length && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Button
            size="sm"
            disabled={
              !currentWeek ||
              !reusable.length ||
              reusable.length > 200 ||
              confirm.isPending
            }
            onClick={() => confirm.mutate()}
          >
            按上次设置确认未来 4 周
          </Button>
          <p className="text-xs text-muted-foreground">
            将批量确认 {reusable.length} 人（另有{" "}
            {eligible.length - reusable.length} 人尚无历史记录，需单独设置）。
          </p>
          {reusable.length > 200 && (
            <p className="text-sm text-muted-foreground">
              每次最多确认 200 人，请先按部门筛选。
            </p>
          )}
          <SetupError error={confirm.error} />
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        格子数值为「已占用 / 可用」（含其他工作、已分配及暂留）。
      </p>
      <WorkloadLegend />
      {chart.isPending ? (
        <ProfileLoading />
      ) : chart.isError ? (
        <ProfileError retry={() => chart.refetch()} />
      ) : (
        <TeamWorkloadGantt
          rows={chart.data.filter(visible)}
          current={currentWeek ?? ""}
          actions={actions}
          onSelect={(selection) => openDialog({ mode: "details", selection })}
        />
      )}
      <Dialog
        open={!!dialog}
        onOpenChange={(open) => {
          if (!open) setDialog(null)
        }}
      >
        <DialogContent
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto"
          onCloseAutoFocus={(event) => {
            if (dialogOpener.current?.isConnected) {
              event.preventDefault()
              dialogOpener.current.focus({ preventScroll: true })
            }
          }}
        >
          {dialog?.mode === "details" && (
            <WorkloadDetails
              selection={dialog.selection}
              actions={actions(
                dialog.selection.row,
                dialog.selection.week,
                true,
              )}
            />
          )}
          {dialog?.mode === "dates" && (
            <>
              <DialogTitle className="pr-6">
                不可用日期 · {dialog.row.employee_name}
              </DialogTitle>
              {canEdit(dialog.row) && (
                <DialogDescription>
                  保存后将通知本人，不会自动调整已有任务。
                </DialogDescription>
              )}
              <Unavailability
                orgId={orgId}
                employeeId={dialog.row.employee_id}
                startWeek={dialog.week}
                canWrite={canEdit(dialog.row)}
              />
            </>
          )}
          {dialog?.mode === "edit" && canEdit(dialog.row) && (
            <>
              <DialogTitle className="pr-6">
                设置周投入 · {dialog.row.employee_name}
              </DialogTitle>
              <DialogDescription>
                填写平台外工作占比，平台内任务会自动累加，保存后通知本人。
              </DialogDescription>
              <Adjustment
                orgId={orgId}
                workload={dialog.row}
                week={dialog.week}
                close={() => setDialog(null)}
              />
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Adjustment({
  orgId,
  workload,
  week,
  close,
}: {
  orgId: string
  workload: WorkloadPublic
  week: string
  close: () => void
}) {
  const client = useQueryClient()
  const [percent, setPercent] = useState(
    String(workload.last_confirmed?.percent ?? ""),
  )
  const [start, setStart] = useState(week)
  const [end, setEnd] = useState(week)
  const save = useMutation({
    mutationFn: () =>
      CapacityService.setCapacityWeeks({
        path: { org_id: orgId },
        body: {
          items: [
            {
              employee_id: workload.employee_id,
              other_percent: Number(percent),
            },
          ],
          start_week: start,
          end_week: end,
        },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
      close()
    },
  })
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <fieldset disabled={save.isPending} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="start-week">开始周</Label>
            <Input
              id="start-week"
              type="date"
              min="1970-01-05"
              step={7}
              required
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="end-week">结束周</Label>
            <Input
              id="end-week"
              type="date"
              min={start}
              step={7}
              required
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="base-percent">其他工作占比（%）</Label>
          <Input
            id="base-percent"
            type="number"
            min={0}
            max={200}
            step={1}
            required
            value={percent}
            onChange={(event) => setPercent(event.target.value)}
          />
        </div>
        <SetupError error={save.error} />
        <Button type="submit">
          {save.isPending ? "正在保存…" : "确认设置"}
        </Button>
      </fieldset>
    </form>
  )
}
