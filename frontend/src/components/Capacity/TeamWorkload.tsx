import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
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
import { addWeeks, weekLabel, weekLoadLabel } from "./shared"

export function TeamWorkload({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const { employeeId, managedDepartmentIds } = useOrg()
  const [department, setDepartment] = useState("all")
  const [editing, setEditing] = useState<WorkloadPublic | null>(null)
  const query = useQuery({
    queryKey: ["capacity", orgId, "team"],
    queryFn: async () =>
      (await CapacityService.listWorkloads({ path: { org_id: orgId } })).data,
  })
  const eligible = (query.data?.data ?? []).filter(
    (row) =>
      row.employee_id !== employeeId &&
      row.department_id &&
      managedDepartmentIds.includes(row.department_id) &&
      (department === "all" || row.department_id === department),
  )
  const reusable = eligible.filter((row) => row.last_confirmed !== null)
  const confirm = useMutation({
    mutationFn: () =>
      CapacityService.setCapacityWeeks({
        path: { org_id: orgId },
        body: {
          items: reusable.map((row) => ({
            employee_id: row.employee_id,
            other_percent: row.last_confirmed!.percent,
          })),
          start_week: reusable[0].weeks[0].week_start,
          end_week: addWeeks(reusable[0].weeks[0].week_start, 3),
        },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
    },
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  const departments = new Map(
    query.data.data
      .filter((row) => row.department_id)
      .map((row) => [row.department_id!, row.department_name!]),
  )
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        这里按周显示本周和后面 7
        周经理确认的投入比例，只是参考，不是排班承诺；能否承接以部门经理的正式答复为准。
      </p>
      {!managedDepartmentIds.length && (
        <p className="text-sm text-muted-foreground">
          企业管理员可查看团队负荷，调整由管辖经理确认。
        </p>
      )}
      <div className="max-w-xs space-y-2">
        <Label htmlFor="capacity-department">部门筛选</Label>
        <Select value={department} onValueChange={setDepartment}>
          <SelectTrigger id="capacity-department">
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
      {!!managedDepartmentIds.length && (
        <div className="space-y-2">
          <Button
            disabled={
              !reusable.length || reusable.length > 200 || confirm.isPending
            }
            onClick={() => confirm.mutate()}
          >
            沿用上次确认值确认未来 4 周
          </Button>
          <p className="text-sm text-muted-foreground">
            为当前筛选范围内可设置的 {reusable.length} 人确认本周起 4 周；
            {eligible.length - reusable.length}{" "}
            人没有上次确认值，未选入，请先逐人设置。
          </p>
          {reusable.length > 200 && (
            <p className="text-sm text-muted-foreground">
              每次最多确认 200 人，请先按部门筛选。
            </p>
          )}
          <SetupError error={confirm.error} />
        </div>
      )}
      <p className="text-sm text-muted-foreground">
        周格显示占用 / 可用；占用包含其他工作和平台分配。
      </p>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">团队负荷，按部门、姓名排序</caption>
          <thead className="bg-muted/40">
            <tr>
              {[
                "姓名",
                "部门",
                "Jira 参考值",
                "上次确认",
                ...(query.data.data[0]?.weeks.map((week) =>
                  weekLabel(week.week_start),
                ) ?? []),
                "操作",
              ].map((label) => (
                <th key={label} className="whitespace-nowrap p-3">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {query.data.data
              .filter(
                (row) =>
                  department === "all" || row.department_id === department,
              )
              .map((row) => (
                <tr key={row.employee_id} className="border-t">
                  <th scope="row" className="whitespace-nowrap p-3 font-medium">
                    {row.employee_name}
                  </th>
                  <td className="p-3">{row.department_name ?? "未分配部门"}</td>
                  <td className="min-w-40 p-3">
                    {row.jira_task_count === null
                      ? "无，未接 Jira 或未关联 Jira 账号"
                      : `${row.jira_task_count} 件未完成`}
                  </td>
                  <td className="whitespace-nowrap p-3 tabular-nums">
                    {row.last_confirmed
                      ? `${row.last_confirmed.percent}%（${weekLabel(row.last_confirmed.week_start)}）`
                      : "暂无确认"}
                  </td>
                  {row.weeks.map((week) => (
                    <td
                      key={week.week_start}
                      className="whitespace-nowrap p-3 tabular-nums"
                    >
                      <span
                        className={
                          week.status === "confirmed"
                            ? "font-medium"
                            : "text-muted-foreground"
                        }
                      >
                        {weekLoadLabel(week)}
                      </span>
                      {week.legacy_start && (
                        <p className="text-xs text-muted-foreground">
                          开始日期未确认
                        </p>
                      )}
                    </td>
                  ))}
                  <td className="p-3">
                    {row.employee_id !== employeeId &&
                      row.department_id &&
                      managedDepartmentIds.includes(row.department_id) && (
                        <Button
                          variant="outline"
                          onClick={() => setEditing(row)}
                        >
                          设置
                        </Button>
                      )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        {!query.data.count && (
          <p className="p-4 text-muted-foreground">范围内暂无员工。</p>
        )}
      </div>
      <Dialog
        open={!!editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null)
        }}
      >
        <DialogContent>
          <DialogTitle>设置周投入 · {editing?.employee_name}</DialogTitle>
          <DialogDescription>
            设置平台外其他工作占比，平台内分配另行计入。改后会通知本人。
          </DialogDescription>
          {editing && (
            <Adjustment
              key={editing.employee_id}
              orgId={orgId}
              workload={editing}
              close={() => setEditing(null)}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Adjustment({
  orgId,
  workload,
  close,
}: {
  orgId: string
  workload: WorkloadPublic
  close: () => void
}) {
  const client = useQueryClient()
  const [percent, setPercent] = useState(
    String(workload.last_confirmed?.percent ?? ""),
  )
  const [start, setStart] = useState(workload.weeks[0].week_start)
  const [end, setEnd] = useState(workload.weeks[0].week_start)
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
