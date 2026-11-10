import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useCallback, useState } from "react"
import {
  type BotSettings,
  BotSettingsService,
  ModelSettingService,
} from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"

export function BotPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <Settings key={org.id} orgId={org.id} />
}

function Settings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const [draft, setDraft] = useState<BotSettings | null>(null)
  const setting = useQuery({
    queryKey: ["bot-settings", orgId],
    queryFn: async () =>
      (await BotSettingsService.settingsGetBotSettings({ path })).data,
  })
  const model = useQuery({
    queryKey: ["model-setting", orgId],
    queryFn: async () =>
      (await ModelSettingService.settingGetModelSetting({ path })).data,
  })
  const save = useMutation({
    mutationFn: () =>
      BotSettingsService.settingsPutBotSettings({
        path,
        body: draft ?? setting.data ?? {},
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["bot-settings", orgId], data)
      setDraft(null)
    },
  })
  const resetManual = useCallback(() => setDraft(null), [])
  const pageDraft = usePageDraft(orgId, "update_bot_settings", resetManual)
  if (setting.isPending || model.isPending) return <ProfileLoading />
  if (setting.isError || model.isError)
    return (
      <ProfileError
        retry={() => {
          setting.refetch()
          model.refetch()
        }}
      />
    )
  const value = pageDraft.values(draft ?? setting.data)
  return (
    <div className="max-w-3xl space-y-6">
      <header className="border-l-4 border-primary pl-4">
        <p className="text-xs text-muted-foreground">管理</p>
        <h1 className="mt-2 text-3xl font-semibold">内置机器人</h1>
        <p className="mt-3 text-muted-foreground">
          帮助员工讲清经历，提交前由本人确认。
        </p>
      </header>
      <section
        className="space-y-3 rounded-xl border bg-muted/20 p-5"
        aria-label="机器人模型"
      >
        <h2 className="font-semibold">当前模型</h2>
        <p>
          {model.data.enabled
            ? model.data.model
            : "尚未启用模型，员工仍可在画像页直接修改。"}
        </p>
        {model.data.kind === "chatgpt_subscription" && (
          <p>
            全企业员工共用这一个 ChatGPT
            订阅账号的五小时和每周额度。起草任务优先，额度用完后请稍后再试。
          </p>
        )}
        <p className="text-sm text-muted-foreground">
          每名员工每小时最多三十轮对话。
        </p>
        <Link className="underline" to="/admin/model">
          前往模型设置
        </Link>
      </section>
      <PageDraft
        orgId={orgId}
        draft={pageDraft.draft}
        error={pageDraft.error}
      />
      <form
        className="space-y-6 rounded-xl border p-5"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <fieldset
          className="space-y-6"
          disabled={pageDraft.blocked || save.isPending}
        >
          <DraftField changed={pageDraft.changed("question_guide")}>
            <label className="block space-y-2">
              提问指南
              <textarea
                aria-label="提问指南"
                className="min-h-40 w-full rounded-md border bg-background p-3"
                maxLength={4000}
                value={value.question_guide ?? ""}
                disabled={save.isPending}
                onChange={(event) => {
                  setDraft({ ...value, question_guide: event.target.value })
                  save.reset()
                }}
                placeholder="例如：请结合具体项目询问跨部门合作经历。"
              />
            </label>
          </DraftField>
          <p className="text-sm text-muted-foreground">
            保存后从下一轮对话生效，不会改变身份、权限或提交确认规则。
          </p>
          <details className="space-y-3 rounded-lg border p-4">
            <summary className="cursor-pointer font-medium">
              高级设置：聊天原文保留
            </summary>
            <DraftField changed={pageDraft.changed("retention_days")}>
              <label className="block space-y-2" htmlFor="bot-retention">
                聊天原文保留天数
                <Input
                  id="bot-retention"
                  type="number"
                  min={0}
                  max={30}
                  step={1}
                  required
                  value={value.retention_days ?? 0}
                  disabled={save.isPending}
                  onChange={(event) => {
                    setDraft({
                      ...value,
                      retention_days: Number(event.target.value),
                    })
                    save.reset()
                  }}
                />
              </label>
            </DraftField>
            <p className="text-sm text-muted-foreground">
              默认零天：会话闲置两小时后清除聊天原文，仅保留会话元数据。可设置一至三十天；消息加密保存，员工确认过的自述证据不受聊天清理影响。
            </p>
          </details>
          <SetupError error={save.error} />
          <Button disabled={save.isPending}>
            {save.isPending ? "正在保存…" : "保存"}
          </Button>
          {save.isSuccess && <p role="status">机器人设置已保存</p>}
        </fieldset>
      </form>
      <ChangeHistory targetType="bot_setting" />
    </div>
  )
}
