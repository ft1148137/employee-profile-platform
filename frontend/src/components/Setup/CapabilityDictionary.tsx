import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useLocation } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useState } from "react"
import { CapabilitiesService } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { OrgState } from "@/components/Profile/OrgState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { CapabilityBrowser } from "./CapabilityBrowser"
import { DictionaryEditor } from "./DictionaryEditor"

export function CapabilityDictionary() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  return (
    <Dictionary
      key={org.id}
      orgId={org.id}
      admin={roles.includes("org_admin")}
      showStats={roles.includes("org_admin") || roles.includes("dept_manager")}
    />
  )
}

function Dictionary({
  orgId,
  admin,
  showStats,
}: {
  orgId: string
  admin: boolean
  showStats: boolean
}) {
  const client = useQueryClient()
  const suggestion = usePageDraft(orgId, "update_capability_dictionary")
  const openEditor = useLocation({
    select: (location) => admin && location.hash === "dictionary-editor",
  })
  const [editing, setEditing] = useState(openEditor)
  const [managing, setManaging] = useState(openEditor)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const current = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
    retry: false,
  })
  const download = useMutation({
    mutationFn: async () => {
      const { data } = await CapabilitiesService.exportDictionary({
        path: { org_id: orgId },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("词典下载失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = "本企业能力词典.yaml"
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    onError: setError,
  })
  const missingDictionary =
    current.error instanceof AxiosError &&
    current.error.response?.status === 404
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
      <SetupError error={missingDictionary ? null : current.error} />
      {missingDictionary && (
        <p>
          尚未启用能力词典。
          {admin ? "请点击编辑词典创建。" : "请联系企业管理员。"}
        </p>
      )}
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
          onClick={() => download.mutate()}
        >
          导出当前词典
        </Button>
      </div>
      {admin && (
        <Button
          variant="outline"
          onClick={() => {
            setManaging(!managing)
            setEditing(!managing)
          }}
        >
          {managing ? "返回浏览" : "编辑词典"}
        </Button>
      )}
      {!managing && !suggestion.draft && current.data && (
        <CapabilityBrowser
          orgId={orgId}
          capabilities={current.data.data}
          showStats={showStats}
        />
      )}
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      {admin && (
        <section className="space-y-3 rounded-xl border p-4">
          <p>要导入任意格式的词典，在管理助手里上传文件</p>
          <Button variant="outline" asChild>
            <Link
              to="/admin/assistant"
              search={{ prompt: "帮我按这份材料导入能力词典" }}
            >
              在管理助手中导入词典
            </Link>
          </Button>
        </section>
      )}
      {admin && ((managing && editing) || suggestion.draft) && (
        <DictionaryEditor
          key={
            suggestion.draft
              ? `${suggestion.draft.id}:${suggestion.draft.revision}`
              : "manual"
          }
          orgId={orgId}
          suggestion={suggestion}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            setManaging(false)
            setSaved(true)
            client.invalidateQueries()
          }}
        />
      )}
      <SetupError error={error} />
      {saved && <p role="status">新词典已启用。</p>}
      <ChangeHistory targetType="capability_schema_version" />
    </div>
  )
}
