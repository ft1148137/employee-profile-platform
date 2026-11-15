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
import { WorkloadBand } from "./shared"

export function TeamWorkload({ orgId }: { orgId: string }) {
  const { employeeId, managedDepartmentIds } = useOrg()
  const [department, setDepartment] = useState("all")
  const [editing, setEditing] = useState<WorkloadPublic | null>(null)
  const query = useQuery({
    queryKey: ["capacity", orgId, "team"],
    queryFn: async () =>
      (await CapacityService.listWorkloads({ path: { org_id: orgId } })).data,
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
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">团队负荷，按部门、姓名排序</caption>
          <thead className="bg-muted/40">
            <tr>
              {[
                "姓名",
                "部门",
                "Jira 参考值",
                "经理确认",
                "负荷状态",
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
                  <td className="p-3">
                    {row.jira_task_count === null
                      ? "无，未接 Jira 或未关联 Jira 账号"
                      : `${row.jira_task_count} 件未完成`}
                  </td>
                  <td className="p-3 tabular-nums">
                    {row.confirmed_percent === null
                      ? "未确认"
                      : `${row.confirmed_percent}%`}
                  </td>
                  <td className="p-3">
                    <WorkloadBand band={row.band} />
                  </td>
                  <td className="p-3">
                    {row.employee_id !== employeeId &&
                      row.department_id &&
                      managedDepartmentIds.includes(row.department_id) && (
                        <Button
                          variant="outline"
                          onClick={() => setEditing(row)}
                        >
                          调整
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
          <DialogTitle>调整负荷 · {editing?.employee_name}</DialogTitle>
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
  const [percent, setPercent] = useState(String(workload.base_percent ?? ""))
  const [note, setNote] = useState("")
  const save = useMutation({
    mutationFn: () =>
      CapacityService.adjustWorkload({
        path: { org_id: orgId, employee_id: workload.employee_id },
        body: { base_percent: Number(percent), note: note.trim() || null },
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
        <div className="space-y-2">
          <Label htmlFor="adjustment-note">调整说明（选填）</Label>
          <textarea
            className="w-full rounded-md border bg-transparent p-3 text-sm"
            id="adjustment-note"
            maxLength={500}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        <SetupError error={save.error} />
        <Button type="submit">
          {save.isPending ? "正在保存…" : "确认调整"}
        </Button>
      </fieldset>
    </form>
  )
}
