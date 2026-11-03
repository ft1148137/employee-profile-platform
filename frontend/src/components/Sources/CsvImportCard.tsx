import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useState } from "react"
import { SourcesService, type WorkRecordIssue } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LastSyncStatus, useSourceRuns } from "./LastSyncStatus"

export function CsvImportCard({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const runs = useSourceRuns(orgId)
  const [issues, setIssues] = useState<WorkRecordIssue[]>([])
  const upload = useMutation({
    mutationFn: (file: File) =>
      SourcesService.importWorkRecords({
        path: { org_id: orgId },
        body: { file },
      }),
    onSuccess: ({ data }) => {
      setIssues(data.issues)
      client.invalidateQueries({ queryKey: ["source-runs", orgId] })
    },
    onError: (error) => {
      if (
        error instanceof AxiosError &&
        error.response?.status === 422 &&
        error.response.data?.detail?.code === "work_records_invalid"
      ) {
        setIssues(error.response.data.detail.issues)
      }
    },
  })
  const template = useMutation({
    mutationFn: async (format: "xlsx" | "csv") => {
      const { data } = await SourcesService.workRecordsTemplate({
        path: { org_id: orgId },
        query: { format },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("模板下载失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = `工作记录模板.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  const recent =
    runs.data?.filter((run) => run.kind === "csv").slice(0, 3) ?? []
  return (
    <section
      aria-label="工作记录文件"
      className="space-y-4 rounded-xl border p-4 sm:p-6"
    >
      <h3 className="text-lg font-semibold">上传 CSV / Excel 工作记录</h3>
      <p className="text-sm text-muted-foreground">
        没有
        Jira，或工作记录在其他系统（PLM、Git、排班系统等）时，可以上传文件。支持
        CSV、Excel，文件不超过 5 MB。
      </p>
      <p className="text-sm">
        必填列：记录编号、来源、发生时间、执行人账号、标题。
      </p>
      <div className="flex flex-wrap gap-2">
        {(["xlsx", "csv"] as const).map((format) => (
          <Button
            key={format}
            variant="outline"
            disabled={template.isPending}
            onClick={() => template.mutate(format)}
          >
            下载{format === "xlsx" ? " Excel " : " CSV "}模板
          </Button>
        ))}
      </div>
      <div className="space-y-2">
        <Input
          id="work-records"
          className="peer sr-only"
          type="file"
          accept=".csv,.xlsx"
          disabled={upload.isPending}
          onChange={(event) => {
            const file = event.target.files?.[0]
            setIssues([])
            if (file) upload.mutate(file)
            event.target.value = ""
          }}
        />
        <Label
          htmlFor="work-records"
          className="inline-flex h-9 cursor-pointer items-center rounded-md border px-3 peer-focus-visible:ring-2 peer-disabled:opacity-50"
        >
          选择工作记录文件
        </Label>
      </div>
      <SetupError error={upload.error ?? template.error ?? runs.error} />
      {upload.isPending && <p role="status">正在上传并校验…</p>}
      {upload.isSuccess && <p role="status">已开始导入</p>}
      {upload.isError && issues.length > 0 && (
        <p role="alert">文件有错误，整份文件没有导入，请修改后重新上传。</p>
      )}
      {issues.length > 0 && (
        <div className="overflow-x-auto">
          <table
            className="w-full text-left text-sm"
            aria-label="文件错误和提醒"
          >
            <thead>
              <tr>
                {["行号", "列", "类型", "说明"].map((title) => (
                  <th key={title} className="border-b p-2">
                    {title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {issues.map((issue, index) => (
                <tr key={`${issue.row_number}:${issue.column}:${index}`}>
                  <td className="p-2">{issue.row_number ?? "整份文件"}</td>
                  <td className="p-2">{issue.column ?? "—"}</td>
                  <td className="p-2">
                    {issue.level === "error" ? "错误" : "提醒"}
                  </td>
                  <td className="p-2">{issue.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h4 className="text-sm font-medium">最近几次文件导入</h4>
      {recent.length ? (
        recent.map((run) => <LastSyncStatus key={run.id} run={run} />)
      ) : (
        <p className="text-sm text-muted-foreground">还没有导入记录</p>
      )}
    </section>
  )
}
