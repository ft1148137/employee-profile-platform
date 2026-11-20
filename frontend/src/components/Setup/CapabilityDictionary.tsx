import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useLocation } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { ArrowLeft, Download, Pencil, Sparkles } from "lucide-react"
import { useState } from "react"
import { CapabilitiesService } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { TopbarActions } from "@/components/Common/TopbarSlot"
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
    <div className="space-y-4">
      <TopbarActions>
        <div className="flex min-w-0 items-center gap-1 lg:gap-2">
          {current.data && (
            <span className="min-w-0 rounded-md bg-muted px-1 py-1 text-center text-[10px] leading-tight font-medium tabular-nums lg:px-1.5 lg:text-xs">
              <span className="hidden whitespace-nowrap lg:inline">
                当前第 {current.data.schema_version.version} 版 ·{" "}
                {current.data.data.length} 项能力
              </span>
              <span className="lg:hidden">
                <span className="block truncate">
                  第 {current.data.schema_version.version} 版
                </span>
                <span
                  className="block truncate"
                  title={`${current.data.data.length} 项能力`}
                >
                  {current.data.data.length} 项
                </span>
              </span>
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            className="has-[>svg]:px-1 lg:has-[>svg]:px-2.5"
            disabled={!current.data || download.isPending}
            onClick={() => download.mutate()}
            title="导出当前词典"
          >
            <Download aria-hidden="true" />
            <span className="sr-only lg:not-sr-only">导出当前词典</span>
          </Button>
          {admin && (
            <>
              <Button
                variant={managing ? "outline" : "default"}
                size="sm"
                className="has-[>svg]:px-1 lg:has-[>svg]:px-2.5"
                disabled={!!suggestion.draft}
                onClick={() => setManaging(!managing)}
                title={managing ? "返回浏览" : "编辑词典"}
              >
                {managing ? (
                  <ArrowLeft aria-hidden="true" />
                ) : (
                  <Pencil aria-hidden="true" />
                )}
                <span className="sr-only lg:not-sr-only">
                  {managing ? "返回浏览" : "编辑词典"}
                </span>
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="has-[>svg]:px-1 lg:has-[>svg]:px-2.5"
                asChild
              >
                <Link
                  to="/admin/assistant"
                  search={{ prompt: "帮我按这份材料导入能力词典" }}
                  title="用助手导入"
                >
                  <Sparkles aria-hidden="true" />
                  <span className="sr-only lg:not-sr-only">用助手导入</span>
                </Link>
              </Button>
            </>
          )}
        </div>
      </TopbarActions>
      {current.isPending && <p role="status">正在加载词典…</p>}
      <SetupError error={missingDictionary ? null : current.error} />
      {missingDictionary && (
        <p>
          {admin
            ? "尚未配置能力词典。点击「编辑词典」开始创建。"
            : "企业尚未配置能力词典，请联系管理员。"}
        </p>
      )}
      {!managing && !suggestion.draft && current.data && (
        <CapabilityBrowser
          orgId={orgId}
          capabilities={current.data.data}
          showStats={showStats}
          history={
            admin && (
              <ChangeHistory
                targetType="capability_schema_version"
                variant="drawer"
              />
            )
          }
        />
      )}
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      {admin && (managing || suggestion.draft) && (
        <DictionaryEditor
          key={
            suggestion.draft
              ? `${suggestion.draft.id}:${suggestion.draft.revision}`
              : "manual"
          }
          orgId={orgId}
          suggestion={suggestion}
          onClose={() => setManaging(false)}
          onSaved={() => {
            setManaging(false)
            setSaved(true)
            client.invalidateQueries()
          }}
        />
      )}
      <SetupError error={error} />
      {saved && <p role="status">新词典已启用。</p>}
    </div>
  )
}
