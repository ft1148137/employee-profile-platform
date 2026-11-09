import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { ProfileImportService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { useOrg } from "@/hooks/useOrg"

export function ProfileImportsPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <BatchImport key={org.id} orgId={org.id} />
}

function BatchImport({ orgId }: { orgId: string }) {
  const [files, setFiles] = useState<File[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const upload = useMutation({
    mutationFn: async (confirm: boolean) => {
      if (!files.length) throw new Error("请先选择文件")
      const oversized = files.find((file) => file.size > 1024 * 1024)
      if (oversized) throw new Error(`${oversized.name}：文件大小不能超过 1 MB`)
      const result = await ProfileImportService.importUploadProfileImports({
        path: { org_id: orgId },
        body: { files },
        query: { confirm },
      })
      setConfirmed(confirm)
      return result.data
    },
  })
  const download = useMutation({
    mutationFn: async (name: "schema" | "skill" | "example") => {
      const result = await ProfileImportService.importDownloadImportFile({
        path: { name },
        responseType: "blob",
      })
      if (!(result.data instanceof Blob)) throw new Error("下载失败，请重试")
      const url = URL.createObjectURL(result.data)
      const link = document.createElement("a")
      link.href = url
      link.download = {
        schema: "v1.schema.json",
        skill: "SKILL.md",
        example: "example.json",
      }[name]
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  return (
    <div className="max-w-5xl space-y-8">
      <header className="border-l-4 border-primary pl-4">
        <p className="text-xs tracking-widest text-muted-foreground">管理</p>
        <h1 className="mt-2 text-3xl font-semibold">批量导入画像</h1>
        <p className="mt-3 text-muted-foreground">
          管理员导入候选画像 → 员工本人过目并提交 → 经理审批
        </p>
      </header>
      <section
        aria-label="导入说明"
        className="space-y-4 rounded-xl border bg-muted/20 p-6"
      >
        <h2 className="font-semibold">准备 AI 生成的画像</h2>
        <p className="text-sm leading-relaxed">
          每个画像文件可以是一人的对象或多人的数组。每个文件最大 1
          MB，所有文件合计最多 200 人。
          已有草稿或待本人过目请求的员工会跳过；任何错误都会阻止整批导入。导入不调用模型。
        </p>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ["schema", "下载格式说明"],
              ["skill", "下载生成指南"],
              ["example", "下载示例"],
            ] as const
          ).map(([name, label]) => (
            <Button
              key={name}
              variant="outline"
              size="sm"
              disabled={download.isPending}
              onClick={() => download.mutate(name)}
            >
              {label}
            </Button>
          ))}
        </div>
      </section>
      <section className="space-y-4 rounded-xl border p-6">
        <h2 className="font-semibold">上传并核对名单</h2>
        <input
          id="batch-profile-files"
          className="peer sr-only"
          type="file"
          multiple
          accept=".json,application/json"
          disabled={upload.isPending}
          onChange={(event) => {
            setFiles(Array.from(event.target.files ?? []))
            setConfirmed(false)
            upload.reset()
          }}
        />
        <Label
          htmlFor="batch-profile-files"
          className="inline-flex h-9 cursor-pointer items-center rounded-md border px-3 peer-focus-visible:ring-2 peer-disabled:opacity-50"
        >
          选择画像文件（可多选）
        </Label>
        <p className="text-sm text-muted-foreground">
          {files.length
            ? files.map((file) => file.name).join("、")
            : "尚未选择文件"}
        </p>
        <SetupError error={upload.error ?? download.error} />
        {upload.data && (
          <section
            aria-label={confirmed ? "导入结果" : "导入预览"}
            className="space-y-3"
          >
            <p role="status">
              {confirmed
                ? "导入完成，请员工在收件箱过目后提交。"
                : "预览尚未保存，请核对后确认导入。"}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="p-3">姓名</th>
                    <th className="p-3">邮箱</th>
                    <th className="p-3">改动项数</th>
                    <th className="p-3">处理结果</th>
                  </tr>
                </thead>
                <tbody>
                  {upload.data.map((row) => (
                    <tr key={row.employee_email} className="border-b">
                      <td className="p-3">{row.display_name}</td>
                      <td className="p-3">{row.employee_email}</td>
                      <td className="p-3">{row.item_count}</td>
                      <td className="p-3">
                        {
                          {
                            will_create: "将创建",
                            created: "已创建",
                            skipped: "跳过",
                          }[row.status]
                        }
                        {row.reason && (
                          <p className="mt-1 text-muted-foreground">
                            {row.reason}
                          </p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        <div className="flex gap-3">
          <Button
            variant="outline"
            disabled={!files.length || upload.isPending || confirmed}
            onClick={() => upload.mutate(false)}
          >
            预览导入
          </Button>
          {upload.data && !confirmed && !upload.error && (
            <Button
              disabled={
                upload.isPending ||
                upload.data.every((row) => row.status === "skipped")
              }
              onClick={() => upload.mutate(true)}
            >
              确认导入
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}
