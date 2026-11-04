import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"
import { CapabilitiesService, type DictionaryPreview } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"

export function CapabilityDictionary() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  return (
    <Dictionary
      key={org.id}
      orgId={org.id}
      admin={roles.includes("org_admin")}
    />
  )
}

function Dictionary({ orgId, admin }: { orgId: string; admin: boolean }) {
  const client = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [filename, setFilename] = useState("")
  const [preview, setPreview] = useState<DictionaryPreview | null>(null)
  const [redraft, setRedraft] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const current = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
    retry: false,
  })
  const upload = useMutation({
    mutationFn: (file: File) =>
      CapabilitiesService.previewDictionary({
        path: { org_id: orgId },
        body: { file },
      }),
    onMutate: (file) => {
      setFilename(file.name)
      setPreview(null)
      setError(null)
      setSaved(false)
    },
    onSuccess: ({ data }) => setPreview(data),
    onError: setError,
  })
  const save = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("请先上传词典")
      return CapabilitiesService.saveDictionary({
        path: { org_id: orgId },
        body: {
          definitions: preview.definitions,
          expected_version: preview.expected_version,
          redraft,
        },
      })
    },
    onMutate: () => setError(null),
    onSuccess: () => {
      setPreview(null)
      setSaved(true)
      client.invalidateQueries()
    },
    onError: setError,
  })
  const download = useMutation({
    mutationFn: async (format: "yaml" | "xlsx" | "export") => {
      const options = { path: { org_id: orgId }, responseType: "blob" as const }
      const { data } =
        format === "export"
          ? await CapabilitiesService.exportDictionary(options)
          : await CapabilitiesService.dictionaryTemplate({
              ...options,
              query: { format },
            })
      if (!(data instanceof Blob)) throw new Error("词典下载失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download =
        format === "export" ? "本企业能力词典.yaml" : `能力词典模板.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    onError: setError,
  })
  const busy = upload.isPending || save.isPending
  return (
    <div className="max-w-4xl space-y-6 py-6">
      <header className="border-l-4 border-primary pl-4">
        <h2 className="text-2xl font-semibold">能力词典</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          每次保存生成新版本，已有判断和发布记录保留。能力标识
          相同的判断会在新词典中继续显示。
        </p>
      </header>
      {current.isPending && <p role="status">正在加载词典…</p>}
      {current.data && (
        <p className="rounded-xl border bg-muted/20 p-4">
          当前第 {current.data.schema_version.version} 版 ·{" "}
          {current.data.data.length} 项能力
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={!current.data || download.isPending}
          onClick={() => download.mutate("export")}
        >
          导出当前词典
        </Button>
        {admin &&
          (["yaml", "xlsx"] as const).map((format) => (
            <Button
              key={format}
              variant="outline"
              disabled={download.isPending}
              onClick={() => download.mutate(format)}
            >
              下载模板（{format === "xlsx" ? "Excel" : "YAML"}）
            </Button>
          ))}
      </div>
      {admin && (
        <section
          className="space-y-4 rounded-xl border p-6"
          aria-label="上传能力词典"
        >
          <label htmlFor="dictionary-file" className="block font-medium">
            上传 YAML 或 Excel 词典（不超过 2 MB）
          </label>
          <Input
            ref={fileInput}
            hidden
            id="dictionary-file"
            type="file"
            accept=".yaml,.yml,.xlsx"
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
            onClick={() => fileInput.current?.click()}
          >
            选择词典文件
          </Button>
          {filename && (
            <p className="break-all text-sm text-muted-foreground">
              {filename}
            </p>
          )}
          {upload.isPending && <p role="status">正在分析词典…</p>}
          {preview && (
            <section className="space-y-4" aria-label="词典改动预览">
              <h3 className="font-semibold">
                保存为第 {preview.expected_version + 1} 版
              </h3>
              {(
                [
                  ["新增", preview.added],
                  ["修改", preview.modified],
                  ["删除", preview.removed],
                ] as const
              ).map(([label, keys]) => (
                <p key={label}>
                  {label} {keys.length} 项：{keys.join("、") || "无"}
                </p>
              ))}
              {preview.errors.length > 0 && (
                <ul role="alert" className="space-y-2 text-destructive">
                  {preview.errors.map((message, index) => (
                    <li key={`${index}:${message}`}>{message}</li>
                  ))}
                </ul>
              )}
              <p className="text-sm text-muted-foreground">
                删除的能力会移至画像的「已不在当前词典中的能力」。未提交请求会更新，被删除的项会移除；等级失效时员工需重新选择或清空。
              </p>
              <label
                className="flex items-start gap-3"
                htmlFor="dictionary-redraft"
              >
                <Checkbox
                  id="dictionary-redraft"
                  checked={redraft}
                  disabled={busy}
                  onCheckedChange={(value) => setRedraft(value === true)}
                />
                <span>按新词典重新起草还没过目的初始画像</span>
              </label>
              <Button
                disabled={
                  busy ||
                  preview.errors.length > 0 ||
                  !preview.definitions.length
                }
                onClick={() => save.mutate()}
              >
                {save.isPending ? "正在保存…" : "确认保存新版本"}
              </Button>
            </section>
          )}
        </section>
      )}
      <SetupError error={error} />
      {saved && <p role="status">新词典已启用。</p>}
    </div>
  )
}
