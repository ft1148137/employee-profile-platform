import { useQuery } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useId, useState } from "react"
import { z } from "zod"
import { AccountsService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { handleError } from "@/utils"
import { type AccountStatus, accountLabels } from "./labels"

export type IssuableEmployee = {
  id: string
  employee_code: string | null
  display_name: string
  status: AccountStatus
}
type Props = {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  orgId: string
  employees?: IssuableEmployee[]
  onIssued: (count: number) => void
}
const conflictSchema = z.array(
  z.object({
    employee_code: z.string().nullable(),
    display_name: z.string(),
    reason: z.string(),
  }),
)

export function IssueAccountsDialog({
  orgId,
  employees,
  onIssued,
  open: controlledOpen,
  onOpenChange,
}: Props) {
  const [internalOpen, setOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const [busy, setBusy] = useState(false)
  const [issued, setIssued] = useState<number | null>(null)
  const regenerateId = useId()
  const selected = employees?.length ? employees : undefined
  const [regenerate, setRegenerate] = useState(false)
  const [error, setError] = useState("")
  const [conflicts, setConflicts] = useState<z.infer<typeof conflictSchema>>([])
  const needsRegeneration =
    selected?.some((e) => e.status === "pending_first_login") ?? false
  function close(value: boolean) {
    if (busy) return
    setOpen(value)
    onOpenChange?.(value)
    if (!value) {
      if (issued !== null) onIssued(issued)
      setIssued(null)
      setRegenerate(false)
      setError("")
      setConflicts([])
    }
  }
  const count = useQuery({
    queryKey: ["accounts", orgId, "unissued-count"],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { status: "none", employment_status: "active", limit: 1 },
        })
      ).data.count,
    enabled: open && !selected,
  })
  async function issue() {
    setBusy(true)
    setError("")
    setConflicts([])
    try {
      const response = await AccountsService.issueAccounts({
        path: { org_id: orgId },
        body: {
          employee_ids: selected?.map((e) => e.id),
          regenerate_pending: regenerate,
        },
        responseType: "blob",
      })
      let filename = "账号.xlsx"
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(
        response.headers["content-disposition"] ?? "",
      )?.[1]
      if (encoded) {
        try {
          filename = decodeURIComponent(encoded)
        } catch {
          /* Use the fallback for malformed headers. */
        }
      }
      const url = URL.createObjectURL(response.data)
      try {
        const link = document.createElement("a")
        link.href = url
        link.download = filename
        link.click()
      } finally {
        setTimeout(() => URL.revokeObjectURL(url), 60_000)
      }
      setIssued(Number(response.headers["x-issued-count"]))
    } catch (err) {
      if (err instanceof AxiosError && err.response) {
        if (err.response.data instanceof Blob) {
          try {
            err.response.data = JSON.parse(await err.response.data.text())
          } catch {
            /* Fall back to the common error message. */
          }
        }
        const detail: unknown = err.response.data?.detail
        const parsed = conflictSchema.safeParse(detail)
        if (err.response.status === 409 && parsed.success)
          setConflicts(parsed.data)
        else if (typeof detail === "string") setError(detail)
        else handleError.call(setError, err)
      } else
        handleError.call(setError, err instanceof Error ? err : new Error())
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onOpenChange={close}>
      {controlledOpen === undefined && (
        <Button onClick={() => setOpen(true)}>
          发放账号{selected ? `（已选 ${selected.length} 人）` : ""}
        </Button>
      )}
      <DialogContent
        className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
        showCloseButton={!busy}
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault()
        }}
        onInteractOutside={(e) => {
          if (busy) e.preventDefault()
        }}
      >
        <DialogHeader>
          <DialogTitle>发放账号</DialogTitle>
          <DialogDescription>
            将为所选员工创建登录账号，并下载含初始密码的表格。
          </DialogDescription>
        </DialogHeader>
        {issued !== null ? (
          <div
            role="status"
            className="space-y-4 rounded-lg border bg-muted/30 p-4"
          >
            <p className="font-semibold">已生成 {issued} 个账号。</p>
            <p>
              账号表仅支持下载一次，内含初始密码，请私发给对应员工（首次登录须改密）。
            </p>
          </div>
        ) : (
          <>
            {selected ? (
              <DataTable
                data={selected}
                columns={[
                  { accessorKey: "employee_code", header: "工号" },
                  { accessorKey: "display_name", header: "姓名" },
                  {
                    accessorKey: "status",
                    header: "账号状态",
                    cell: ({ row }) => accountLabels[row.original.status],
                  },
                ]}
              />
            ) : count.isError ? (
              <div role="alert">
                无法加载生成范围。
                <Button variant="outline" onClick={() => count.refetch()}>
                  重试
                </Button>
              </div>
            ) : (
              <p>
                {count.isPending
                  ? "正在统计可发放账号的员工…"
                  : `将为 ${count.data} 名未开通账号的在职员工发放账号`}
              </p>
            )}
            {!selected && count.data === 0 && (
              <p>所有在职员工均已开通账号，无需发放。</p>
            )}
            {needsRegeneration && (
              <>
                <label
                  htmlFor={regenerateId}
                  className="flex items-start gap-3"
                >
                  <Checkbox
                    id={regenerateId}
                    checked={regenerate}
                    onCheckedChange={(value) => setRegenerate(value === true)}
                    disabled={busy}
                  />
                  <span>为「待首次登录」账号重置初始密码（旧密码将失效）</span>
                </label>
                {!regenerate && (
                  <p className="text-sm text-muted-foreground">
                    请勾选重新生成，或取消勾选这些人
                  </p>
                )}
              </>
            )}
            {conflicts.length > 0 && (
              <div role="alert">
                <p>以下人员存在冲突，本次未发放任何账号，请处理后重试：</p>
                <DataTable
                  data={conflicts}
                  columns={[
                    { accessorKey: "employee_code", header: "工号" },
                    { accessorKey: "display_name", header: "姓名" },
                    { accessorKey: "reason", header: "原因" },
                  ]}
                />
              </div>
            )}
            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
            <Button
              disabled={
                busy ||
                (needsRegeneration && !regenerate) ||
                (!selected && (!count.data || count.isError))
              }
              onClick={issue}
            >
              {busy ? "正在生成…" : "确认生成并下载"}
            </Button>
          </>
        )}
        <Button variant="outline" disabled={busy} onClick={() => close(false)}>
          {issued === null ? "取消" : "关闭"}
        </Button>
      </DialogContent>
    </Dialog>
  )
}
