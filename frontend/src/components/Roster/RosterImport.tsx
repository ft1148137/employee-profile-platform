import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useId, useRef, useState } from "react"
import { type RosterImportPreview, RosterService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { ImportHistory } from "./ImportHistory"
import { RosterPreview } from "./RosterPreview"

export function RosterImport({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const fileId = useId()
  const input = useRef<HTMLInputElement>(null)
  const [filename, setFilename] = useState("")
  const confirmId = useId()
  const [preview, setPreview] = useState<RosterImportPreview | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [message, setMessage] = useState("")
  const [applied, setApplied] = useState(false)
  const [stale, setStale] = useState(false)
  const refresh = () =>
    Promise.all(
      ["roster-history", "departments", "accounts", "setup", "employees"].map(
        (key) => client.invalidateQueries({ queryKey: [key, orgId] }),
      ),
    )
  const upload = useMutation({
    mutationFn: (file: File) => {
      setFilename(file.name)
      setPreview(null)
      setConfirmed(false)
      setApplied(false)
      setStale(false)
      setError(null)
      setMessage("")
      return RosterService.uploadImport({
        path: { org_id: orgId },
        body: { file },
      })
    },
    onSuccess: ({ data }) => {
      setPreview(data)
      refresh()
    },
    onError: setError,
  })
  const apply = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("请先上传名单")
      setError(null)
      setMessage("")
      return RosterService.applyImport({
        path: { org_id: orgId, id: preview.id },
        body: {
          base_revision: preview.base_revision,
          confirm_mass_deactivation: confirmed,
        },
      })
    },
    onSuccess: () => {
      setApplied(true)
      refresh()
    },
    onError: (err) => {
      if (err instanceof AxiosError && err.response?.status === 409) {
        setStale(true)
        setMessage("名单在你预览之后被更新过，请重新上传")
      } else if (
        err instanceof AxiosError &&
        Array.isArray(err.response?.data?.detail)
      ) {
        setMessage(
          err.response.data.detail
            .filter((item: unknown) => typeof item === "string")
            .join("；"),
        )
      } else setError(err)
    },
  })
  const template = useMutation({
    mutationFn: async (format: "xlsx" | "csv") => {
      setError(null)
      const { data } = await RosterService.downloadTemplate({
        path: { org_id: orgId },
        query: { format },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("模板下载失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = `员工名单模板.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    onError: setError,
  })
  const busy = upload.isPending || apply.isPending
  return (
    <div className="min-w-0 space-y-6">
      <p className="text-sm text-muted-foreground">
        请上传全量名单，邮箱必填并作为登录名。名单里没有的在职员工会被停用，画像和证据保留。支持
        Excel、CSV，文件不超过 5 MB。
      </p>
      <div className="flex flex-wrap gap-2">
        {(["xlsx", "csv"] as const).map((format) => (
          <Button
            key={format}
            variant="outline"
            disabled={template.isPending}
            onClick={() => template.mutate(format)}
          >
            下载模板（{format === "xlsx" ? "Excel" : "CSV"}）
          </Button>
        ))}
      </div>
      <section
        aria-label="上传名单"
        className="rounded-xl border-2 border-dashed bg-muted/20 p-5"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault()
          const file = event.dataTransfer.files[0]
          if (file && !busy) upload.mutate(file)
        }}
      >
        <label htmlFor={fileId} className="mb-3 block font-medium">
          拖拽名单到这里，或选择文件上传
        </label>
        <Input
          ref={input}
          hidden
          id={fileId}
          type="file"
          accept=".xlsx,.csv"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) upload.mutate(file)
            event.target.value = ""
          }}
        />
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          选择名单文件
        </Button>
        {filename && (
          <p className="mt-2 break-all text-sm text-muted-foreground">
            {filename}
          </p>
        )}
      </section>
      {upload.isPending && <p role="status">正在上传并分析名单…</p>}
      <SetupError error={error} />
      {message && (
        <p role="alert" className="text-destructive">
          {message}
        </p>
      )}
      {preview && (
        <>
          <RosterPreview preview={preview} />
          {preview.requires_mass_deactivation_confirm && (
            <div
              role="alert"
              className="space-y-3 rounded-lg border-2 border-destructive bg-destructive/5 p-4"
            >
              <p className="font-semibold text-destructive">
                注意：本次将停用 {preview.summary.deactivated}{" "}
                名员工及其账号，请核对完整名单。
              </p>
              <label htmlFor={confirmId} className="flex items-start gap-3">
                <Checkbox
                  id={confirmId}
                  checked={confirmed}
                  disabled={busy || applied || stale}
                  onCheckedChange={(value) => setConfirmed(value === true)}
                />
                <span>我确认这些员工已离职，停用他们的账号</span>
              </label>
            </div>
          )}
          {applied ? (
            <p
              role="status"
              className="rounded-lg border bg-primary/5 p-4 font-medium"
            >
              导入成功，员工名单和部门已更新。
            </p>
          ) : (
            <Button
              disabled={
                busy ||
                stale ||
                !preview.can_apply ||
                (preview.requires_mass_deactivation_confirm && !confirmed)
              }
              onClick={() => apply.mutate()}
            >
              {apply.isPending ? "正在导入…" : "确认导入"}
            </Button>
          )}
        </>
      )}
      <ImportHistory orgId={orgId} />
    </div>
  )
}
