import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { AccountsService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"

export function EmployeePicker({
  orgId,
  account,
  pending,
  error,
  onSelect,
  onClose,
}: {
  orgId: string
  account: string
  pending: boolean
  error: Error | null
  onSelect: (employeeId: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState("")
  const [skip, setSkip] = useState(0)
  const employees = useQuery({
    queryKey: ["accounts", orgId, "identity-picker", q, skip],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { q, skip, limit: 20 },
        })
      ).data,
  })
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent showCloseButton={!pending}>
        <DialogTitle>选择员工</DialogTitle>
        <DialogDescription>
          为账号「{account}」选择员工，请核对工号和部门。
        </DialogDescription>
        <Input
          aria-label="搜索员工姓名或工号"
          placeholder="搜索姓名或工号"
          value={q}
          disabled={pending}
          onChange={(e) => {
            setQ(e.target.value)
            setSkip(0)
          }}
        />
        <SetupError error={error ?? employees.error} />
        {employees.isError && (
          <Button onClick={() => employees.refetch()}>重试</Button>
        )}
        {employees.isPending && <p role="status">正在加载员工…</p>}
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {employees.data?.data.map((employee) => (
            <Button
              key={employee.id}
              variant="outline"
              className="h-auto w-full justify-start whitespace-normal text-left"
              disabled={pending}
              onClick={() => onSelect(employee.id)}
            >
              {employee.display_name}（{employee.employee_code ?? "无工号"}） ·{" "}
              {employee.department?.name ?? "未分配部门"} ·{" "}
              {employee.employment_status === "active" ? "在职" : "离职"}
            </Button>
          ))}
          {employees.data?.count === 0 && <p>没有找到员工</p>}
        </div>
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            disabled={pending || skip === 0}
            onClick={() => setSkip(skip - 20)}
          >
            上一页
          </Button>
          <span className="text-sm">共 {employees.data?.count ?? 0} 人</span>
          <Button
            variant="outline"
            disabled={pending || skip + 20 >= (employees.data?.count ?? 0)}
            onClick={() => setSkip(skip + 20)}
          >
            下一页
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
