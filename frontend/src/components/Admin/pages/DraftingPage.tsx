import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  DraftingSettingsService,
  InitialProfilesService,
  ModelSettingService,
} from "@/client"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { DraftingNoteForm } from "./DraftingNoteForm"

export function DraftingPage({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const suggestion = usePageDraft(orgId, "set_auto_drafting")
  const settings = useQuery({
    queryKey: ["drafting-settings", orgId],
    queryFn: async () =>
      (await DraftingSettingsService.settingsGetDraftingSettings({ path }))
        .data,
  })
  const model = useQuery({
    queryKey: ["model-setting", orgId],
    queryFn: async () =>
      (await ModelSettingService.settingGetModelSetting({ path })).data,
  })
  const save = useMutation({
    mutationFn: (enabled: boolean) =>
      DraftingSettingsService.settingsPutDraftingSettings({
        path,
        body: { enabled },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["drafting-settings", orgId], data)
      client.invalidateQueries({ queryKey: ["auto-draft", orgId] })
      client.invalidateQueries({ queryKey: ["admin-overview", orgId] })
    },
  })
  const start = useMutation({
    mutationFn: () =>
      InitialProfilesService.profilesDraft({ path, body: { scope: "all" } }),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["draft-progress", orgId] }),
  })
  if (settings.isPending || model.isPending) return <ProfileLoading />
  if (settings.isError || model.isError)
    return (
      <ProfileError
        retry={() => {
          settings.refetch()
          model.refetch()
        }}
      />
    )
  const current = suggestion.values(settings.data)
  return (
    <section aria-label="自动起草" className="rounded-xl border p-6 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <h2 id="auto-drafting-title" className="text-xl font-semibold">
          自动起草
        </h2>
        <DraftField changed={suggestion.changed("enabled")}>
          <Button
            role="switch"
            aria-labelledby="auto-drafting-title"
            aria-checked={current.enabled}
            disabled={
              !model.data?.enabled || save.isPending || suggestion.blocked
            }
            variant={current.enabled ? "default" : "outline"}
            onClick={() => save.mutate(!current.enabled)}
          >
            {current.enabled ? "已开启" : "已关闭"}
          </Button>
        </DraftField>
      </div>
      <p className="text-sm text-muted-foreground">
        打开后，同步工作记录后会自动为所有员工起草或更新画像请求，会消耗模型额度
      </p>
      {!model.data?.enabled && (
        <p role="status">模型未启用，请先在下方配置并启用模型。</p>
      )}
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      <SetupError error={save.error ?? start.error} />
      <div className="flex flex-wrap items-center gap-4">
        <Button
          disabled={
            !settings.data?.enabled ||
            !model.data?.enabled ||
            start.isPending ||
            suggestion.blocked
          }
          onClick={() => {
            if (window.confirm("只覆盖员工还没过目的草稿，确定重新起草全部？"))
              start.mutate()
          }}
        >
          {start.isPending ? "正在排队…" : "开始起草"}
        </Button>
        <a className="text-sm underline" href="/admin">
          查看起草进度
        </a>
      </div>
      {start.isSuccess && (
        <p role="status">已排队 {start.data.data.queued} 名员工</p>
      )}
      <DraftingNoteForm orgId={orgId} />
    </section>
  )
}
