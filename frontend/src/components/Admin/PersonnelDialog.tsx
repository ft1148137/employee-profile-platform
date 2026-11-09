import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useId, useState } from "react"
import {
  type EmployeeAccount,
  EmployeesService,
  type RosterImportPreview,
  RosterService,
} from "@/client"
import { RosterPreview } from "@/components/Roster/RosterPreview"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"

export function PersonnelDialog({
  orgId,
  employee,
  department = "",
  offboard = false,
}: {
  orgId: string
  employee?: EmployeeAccount
  department?: string
  offboard?: boolean
}) {
  const client = useQueryClient()
  const id = useId()
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<RosterImportPreview | null>(null)
  const [managerSearch, setManagerSearch] = useState("")
  const [manager, setManager] = useState("keep")
  const title = offboard ? "离职" : employee ? "调岗" : "新增员工"
  const managers = useQuery({
    queryKey: ["employees", orgId, "manager-search", managerSearch],
    enabled: open && !offboard,
    queryFn: async () =>
      (
        await EmployeesService.readEmployees({
          path: { org_id: orgId },
          query: { q: managerSearch || undefined },
        })
      ).data,
  })
  const create = useMutation({
    mutationFn: async (form: FormData) => {
      const shared = {
        department: String(form.get("department")),
        title: String(form.get("title")) || null,
        ...(manager === "keep"
          ? {}
          : { manager_id: manager === "none" ? null : manager }),
      }
      return (
        await RosterService.previewSingleChange({
          path: { org_id: orgId },
          body:
            offboard && employee
              ? { kind: "offboard", employee_ids: [employee.id] }
              : employee
                ? { kind: "transfer", employee_id: employee.id, ...shared }
                : {
                    kind: "onboard",
                    employee_code: String(form.get("employee_code")),
                    display_name: String(form.get("display_name")),
                    email: String(form.get("email")),
                    ...shared,
                  },
        })
      ).data
    },
    onSuccess: setPreview,
  })
  const apply = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("请先预览")
      return RosterService.applyImport({
        path: { org_id: orgId, id: preview.id },
        body: {
          base_revision: preview.base_revision,
          confirm_mass_deactivation: true,
        },
      })
    },
    onSuccess: () => {
      setOpen(false)
      client.invalidateQueries()
    },
  })
  const busy = create.isPending || apply.isPending
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        setOpen(next)
        setPreview(null)
        setManager("keep")
        create.reset()
        apply.reset()
      }}
    >
      <DialogTrigger asChild>
        <Button variant={offboard ? "destructive" : "outline"}>{title}</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {title}
            {employee ? ` · ${employee.display_name}` : ""}
          </DialogTitle>
          <DialogDescription>
            {offboard
              ? "确认后停用员工及账号，保留画像和证据。"
              : "先核对变动预览，确认后才更新员工名单。部门层级用 / 分隔。"}
          </DialogDescription>
        </DialogHeader>
        {!preview ? (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate(new FormData(event.currentTarget))
            }}
          >
            {!offboard && (
              <>
                {!employee &&
                  [
                    ["employee_code", "工号"],
                    ["display_name", "姓名"],
                    ["email", "邮箱"],
                  ].map(([name, label]) => (
                    <label
                      className="block space-y-2 text-sm"
                      key={name}
                      htmlFor={`${id}-${name}`}
                    >
                      {label}
                      <Input
                        id={`${id}-${name}`}
                        name={name}
                        type={name === "email" ? "email" : "text"}
                        required
                        disabled={busy}
                      />
                    </label>
                  ))}
                <label
                  className="block space-y-2 text-sm"
                  htmlFor={`${id}-department`}
                >
                  部门
                  <Input
                    id={`${id}-department`}
                    name="department"
                    defaultValue={department}
                    required
                    disabled={busy}
                  />
                </label>
                <label
                  className="block space-y-2 text-sm"
                  htmlFor={`${id}-title`}
                >
                  职位
                  <Input
                    id={`${id}-title`}
                    name="title"
                    defaultValue={employee?.title ?? ""}
                    disabled={busy}
                  />
                </label>
                <label
                  className="block space-y-2 text-sm"
                  htmlFor={`${id}-search`}
                >
                  查找上级
                  <Input
                    id={`${id}-search`}
                    value={managerSearch}
                    onChange={(e) => setManagerSearch(e.target.value)}
                    placeholder="输入姓名查找"
                    disabled={busy}
                  />
                </label>
                <label
                  className="block space-y-2 text-sm"
                  htmlFor={`${id}-manager`}
                >
                  上级
                  <select
                    className="h-10 w-full rounded-md border bg-background px-3"
                    id={`${id}-manager`}
                    value={manager}
                    disabled={busy || managers.isError}
                    onChange={(e) => setManager(e.target.value)}
                  >
                    <option value="keep">
                      {employee ? "保持现有上级" : "暂无上级"}
                    </option>
                    <option value="none">清空上级</option>
                    {managers.data?.data
                      .filter(
                        (e) =>
                          e.id !== employee?.id &&
                          e.employment_status === "active",
                      )
                      .map((e) => (
                        <option key={e.id} value={e.id}>
                          {e.display_name}
                          {e.department ? ` · ${e.department.name}` : ""}
                        </option>
                      ))}
                  </select>
                </label>
                <SetupError error={managers.error} />
              </>
            )}
            <Button type="submit" disabled={busy}>
              {create.isPending ? "正在预览…" : "预览变动"}
            </Button>
          </form>
        ) : (
          <>
            <RosterPreview preview={preview} />
            <div className="flex gap-2">
              <Button
                disabled={busy || !preview.can_apply || apply.isError}
                onClick={() => apply.mutate()}
              >
                确认{title}
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setPreview(null)
                  apply.reset()
                }}
              >
                重新填写
              </Button>
            </div>
          </>
        )}
        <SetupError error={create.error ?? apply.error} />
      </DialogContent>
    </Dialog>
  )
}
