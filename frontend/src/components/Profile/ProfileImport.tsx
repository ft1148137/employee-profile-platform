import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapabilitiesService, ProfileImportService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { levelLabel } from "./fields"

export function ProfileImport({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const dictionary = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
    enabled: open,
  })
  const preview = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请先选择文件")
      if (file.size > 1024 * 1024) throw new Error("文件大小不能超过 1 MB")
      return (
        await ProfileImportService.importUploadProfileImport({
          path: { org_id: orgId },
          body: { file },
          query: { confirm: false },
        })
      ).data
    },
  })
  const submit = useMutation({
    mutationFn: async () => {
      if (!file) return
      await ProfileImportService.importUploadProfileImport({
        path: { org_id: orgId },
        body: { file },
        query: { confirm: true },
      })
      for (const key of ["change-requests", "inbox", "profile"])
        client.invalidateQueries({ queryKey: [key, orgId] })
      setOpen(false)
    },
  })
  const download = useMutation({
    mutationFn: async (name: "schema" | "example" | "skill" | "dictionary") => {
      const result =
        name === "dictionary"
          ? await CapabilitiesService.exportDictionary({
              path: { org_id: orgId },
              responseType: "blob",
            })
          : await ProfileImportService.importDownloadImportFile({
              path: { name },
              responseType: "blob",
            })
      if (!(result.data instanceof Blob)) throw new Error("下载失败，请重试")
      const url = URL.createObjectURL(result.data)
      const link = document.createElement("a")
      link.href = url
      link.download = {
        schema: "v1.schema.json",
        example: "example.json",
        skill: "SKILL.md",
        dictionary: "capabilities.yaml",
      }[name]
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  const busy = preview.isPending || submit.isPending
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (busy) return
        setOpen(value)
        setFile(null)
        preview.reset()
        submit.reset()
        download.reset()
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">导入 AI 生成的画像</Button>
      </DialogTrigger>
      <DialogContent
        className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"
        showCloseButton={!busy}
      >
        <DialogTitle>导入 AI 生成的画像</DialogTitle>
        <DialogDescription>
          只导入本人的候选画像，确认后交经理审批，批准前可以撤回。依据仅本人和管辖经理可读。
        </DialogDescription>
        <p className="text-sm">
          当前词典版本：{dictionary.data?.schema_version.version ?? "尚未启用"}
          。不需要配置模型，仅支持单份 JSON，最大 1 MB。
        </p>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ["schema", "下载格式说明"],
              ["skill", "下载生成指南（skill）"],
              ["example", "下载示例"],
              ["dictionary", "下载本企业能力词典"],
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
        <SetupError
          error={
            preview.error ?? submit.error ?? download.error ?? dictionary.error
          }
        />
        <div className="space-y-2">
          <input
            id="profile-import-file"
            className="peer sr-only"
            type="file"
            accept=".json,application/json"
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              preview.reset()
              submit.reset()
            }}
          />
          <Label
            htmlFor="profile-import-file"
            className="inline-flex h-9 cursor-pointer items-center rounded-md border px-3 peer-focus-visible:ring-2 peer-disabled:opacity-50"
          >
            选择画像 JSON 文件
          </Label>
          <p className="text-sm text-muted-foreground">
            {file?.name ?? "尚未选择文件"}
          </p>
        </div>
        {preview.data && (
          <section aria-label="导入预览" className="space-y-3">
            <p className="text-sm text-muted-foreground">
              预览尚未保存，请核对后确认提交。
            </p>
            {preview.data.items.map(({ before, after }) => (
              <article
                key={after.id}
                className="space-y-2 rounded-lg border p-4"
              >
                <h3 className="font-semibold">{after.capability.name}</h3>
                <p>修改前：{before?.statement ?? "暂无判断"}</p>
                <p>候选陈述：{after.after?.statement}</p>
                <p>
                  等级：
                  {after.after?.level
                    ? levelLabel(
                        after.capability.level_rubric,
                        after.after.level,
                      )
                    : "未给等级"}
                </p>
                <p>依据说明：{after.owner_statement}</p>
                {after.uncertainty && <p>不确定之处：{after.uncertainty}</p>}
              </article>
            ))}
          </section>
        )}
        <div className="flex gap-3">
          <Button
            variant="outline"
            disabled={!file || busy}
            onClick={() => preview.mutate()}
          >
            预览导入
          </Button>
          {preview.data && (
            <Button disabled={busy} onClick={() => submit.mutate()}>
              确认提交
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
