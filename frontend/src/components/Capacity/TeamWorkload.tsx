import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { useState } from "react"
import { CapacityService, type WorkloadPublic } from "@/client"
import { HelpTip, SideDrawer } from "@/components/Common/WorkbenchPrimitives"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
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
import { Unavailability } from "./MyWorkload"
import { addWeeks, weekLabel, workloadNotice } from "./shared"
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
  const selected = dialog?.mode === "details" ? dialog.selection : dialog
  const selectedRow = chart.data?.find(
    (row) => row.employee_id === selected?.row.employee_id && visible(row),
  )
  const changeWindow = (weeks: number) => {
    setDialog(null)
    setOffset(weeks)
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
          onClick={() => setDialog({ mode: "edit", row, week })}
        >
          {inDetails ? "设置周投入" : "设置"}
        </Button>
      )}
      {(isAdmin || canEdit(row)) && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setDialog({ mode: "dates", row, week })}
        >
          {inDetails && canEdit(row) ? "登记不可用日期" : "不可用日期"}
        </Button>
      )}
    </div>
  )
  return (
    <div className="min-w-0">
      <div className="flex min-h-11 flex-wrap items-center gap-x-2 gap-y-1 border-surface-border border-b bg-surface px-3 py-1 lg:h-11 lg:flex-nowrap">
        <div className="flex items-center gap-2">
          <Label htmlFor="capacity-department" className="sr-only">
            部门筛选
          </Label>
          <Select
            value={department}
            onValueChange={(value) => {
              setDepartment(value)
              setDialog(null)
            }}
          >
            <SelectTrigger id="capacity-department" className="max-w-36">
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
              onClick={() => changeWindow(offset - 4)}
              aria-label="上一期"
            >
              <ChevronLeft aria-hidden="true" />
              <span className="hidden xl:inline">上一期</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!offset}
              onClick={() => changeWindow(0)}
            >
              回到本周
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => changeWindow(offset + 4)}
              aria-label="下一期"
            >
              <span className="hidden xl:inline">下一期</span>
              <ChevronRight aria-hidden="true" />
            </Button>
            <span
              className="sr-only ml-1 text-xs text-muted-foreground xl:not-sr-only"
              aria-live="polite"
            >
              {weekLabel(fromWeek ?? currentWeek)}起
            </span>
          </fieldset>
        )}
        <HelpTip
          label="团队负荷说明和图例"
          content={
            <div className="space-y-2">
              <p>{workloadNotice}</p>
              <p>展示未来 8 周的投入参考，实际承接以部门经理答复为准。</p>
              {isAdmin && <p>管理员仅可查看，调整需由对应部门经理操作。</p>}
              <p>格子数值为「已占用 / 可用」（含其他工作、已分配及暂留）。</p>
              <WorkloadLegend />
            </div>
          }
        />
        {!isAdmin && !!managedDepartmentIds.length && (
          <div className="ml-auto flex items-center gap-2">
            <HelpTip
              label="批量确认说明"
              content={
                reusable.length > 200
                  ? "每次最多确认 200 人，请先按部门筛选。"
                  : `将批量确认 ${reusable.length} 人（另有 ${eligible.length - reusable.length} 人尚无历史记录，需单独设置）。`
              }
            />
            <Button
              size="sm"
              variant="outline"
              aria-label="按上次设置确认未来 4 周"
              disabled={
                !currentWeek ||
                !reusable.length ||
                reusable.length > 200 ||
                confirm.isPending
              }
              onClick={() => confirm.mutate()}
            >
              沿用上次设置
            </Button>
          </div>
        )}
      </div>
      {confirm.isError && (
        <div className="px-3 py-2">
          <SetupError error={confirm.error} />
        </div>
      )}
      <div className={cn("min-w-0", selectedRow && "md:pr-[min(380px,38vw)]")}>
        {chart.isPending ? (
          <ProfileLoading />
        ) : chart.isError ? (
          <ProfileError retry={() => chart.refetch()} />
        ) : (
          <TeamWorkloadGantt
            rows={chart.data.filter(visible)}
            current={currentWeek ?? ""}
            actions={actions}
            activeEmployee={selectedRow?.employee_id}
            activeWeek={selected?.week}
            onSelect={(selection) => setDialog({ mode: "details", selection })}
          />
        )}
      </div>
      <SideDrawer
        open={!!selectedRow}
        onOpenChange={(open) => {
          if (!open) setDialog(null)
        }}
        modal={false}
        showOverlay={false}
        className="max-md:inset-x-0 max-md:top-auto max-md:bottom-0 max-md:h-[55dvh] max-md:w-full max-md:max-w-none md:w-[min(380px,38vw)] md:max-w-none"
        title={
          selectedRow &&
          `${dialog?.mode === "dates" ? "不可用日期" : dialog?.mode === "edit" ? "设置周投入" : "负荷明细"} · ${selectedRow.employee_name}`
        }
      >
        {selectedRow && dialog && (
          <div
            key={`${selectedRow.employee_id}:${selected?.week}:${dialog.mode}`}
            className="space-y-3"
          >
            {dialog.mode === "details" && (
              <>
                <WorkloadDetails
                  selection={{ ...dialog.selection, row: selectedRow }}
                />
                {actions(selectedRow, dialog.selection.week, true)}
              </>
            )}
            {dialog.mode === "dates" && (
              <>
                {canEdit(selectedRow) && (
                  <p className="text-sm text-muted-foreground">
                    保存后将通知本人，不会自动调整已有任务。
                  </p>
                )}
                <Unavailability
                  orgId={orgId}
                  employeeId={selectedRow.employee_id}
                  startWeek={dialog.week}
                  canWrite={canEdit(selectedRow)}
                />
              </>
            )}
            {dialog.mode === "edit" && canEdit(selectedRow) && (
              <>
                <p className="text-sm text-muted-foreground">
                  填写平台外工作占比，平台内任务会自动累加，保存后通知本人。
                </p>
                <Adjustment
                  orgId={orgId}
                  workload={selectedRow}
                  week={dialog.week}
                  close={() =>
                    setDialog((current) =>
                      current === dialog ? null : current,
                    )
                  }
                />
              </>
            )}
          </div>
        )}
      </SideDrawer>
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
    String(
      workload.weeks.find((item) => item.week_start === week)?.other_percent ??
        workload.last_confirmed?.percent ??
        "",
    ),
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
        <div className="grid gap-3 min-[420px]:grid-cols-2">
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
