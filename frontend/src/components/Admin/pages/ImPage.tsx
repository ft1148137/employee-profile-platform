import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { MessagesSquare, Send, Zap } from "lucide-react"
import { useState } from "react"
import {
  type ImSettingPublic,
  ImSettingsService,
  type ImSettingUpdate,
} from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"
import { IntegrationCard } from "./IntegrationCard"

const platforms = {
  feishu: {
    name: "飞书",
    icon: (
      <Zap
        aria-hidden="true"
        className="size-8 rounded-lg bg-blue-500/10 p-2 text-blue-600 dark:text-blue-300"
      />
    ),
    description: "在飞书私聊中使用内置助手更新画像。",
    fields: { app_id: "应用标识", app_secret: "应用密钥" },
  },
  wecom: {
    name: "企业微信",
    icon: (
      <MessagesSquare
        aria-hidden="true"
        className="size-8 rounded-lg bg-emerald-500/10 p-2 text-emerald-700 dark:text-emerald-300"
      />
    ),
    description: "通过企业微信自建应用长连接接入内置助手。",
    fields: {
      bot_id: "应用标识",
      secret: "应用密钥",
      corp_id: "企业标识（选填）",
      contact_secret: "通讯录密钥（选填）",
    },
  },
  telegram: {
    name: "Telegram",
    icon: (
      <Send
        aria-hidden="true"
        className="size-8 rounded-lg bg-sky-500/10 p-2 text-sky-700 dark:text-sky-300"
      />
    ),
    description: "通过机器人令牌接入内置助手，供员工在 Telegram 中使用。",
    fields: { bot_token: "应用令牌" },
  },
}

export function ImPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <Settings key={org.id} orgId={org.id} />
}

function Settings({ orgId }: { orgId: string }) {
  const assistant = usePageDraft(orgId, "update_im_setting")
  const settings = useQuery({
    queryKey: ["im-settings", orgId],
    queryFn: async () =>
      (
        await ImSettingsService.settingsGetImSettings({
          path: { org_id: orgId },
        })
      ).data,
  })
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  return (
    <div className="space-y-4">
      <h1 className="sr-only">IM 入口</h1>
      <TopbarActions>
        <ChangeHistory targetType="im_setting" variant="drawer" />
      </TopbarActions>
      <PageDraft
        orgId={orgId}
        draft={assistant.draft}
        error={assistant.error}
      />
      <p className="text-sm text-muted-foreground">
        员工绑定聊天账号后，可在对应平台更新画像。
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {settings.data.map((setting) => {
          const platform = platforms[setting.platform]
          const suggestion =
            setting.platform === "feishu" ? assistant : undefined
          return (
            <IntegrationCard
              key={setting.platform}
              name={platform.name}
              icon={platform.icon}
              description={platform.description}
              status={setting.enabled ? "已启用" : "未启用"}
              active={setting.enabled}
              detail={setting.has_credentials ? "凭据已设置" : "尚未设置凭据"}
              suggested={!!suggestion?.draft}
            >
              <PlatformSetting
                key={suggestion?.draft?.id}
                orgId={orgId}
                setting={setting}
                suggestion={suggestion}
              />
            </IntegrationCard>
          )
        })}
      </div>
    </div>
  )
}

function PlatformSetting({
  orgId,
  setting,
  suggestion,
}: {
  orgId: string
  setting: ImSettingPublic
  suggestion?: ReturnType<typeof usePageDraft>
}) {
  const client = useQueryClient()
  const platform = platforms[setting.platform]
  const path = { org_id: orgId, platform: setting.platform }
  const [draft, setDraft] = useState<ImSettingUpdate | null>(null)
  const value = suggestion?.values(draft ?? setting) ?? draft ?? setting
  const [credentials, setCredentials] = useState<Record<string, string>>({})
  const [acknowledged, setAcknowledged] = useState(false)
  const save = useMutation({
    mutationFn: () =>
      ImSettingsService.settingsPutImSetting({
        path,
        body: {
          enabled: value.enabled,
          email_auto_bind: value.email_auto_bind,
          credentials,
          data_transfer_acknowledged: acknowledged,
        },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData<ImSettingPublic[]>(["im-settings", orgId], (rows) =>
        rows?.map((row) => (row.platform === data.platform ? data : row)),
      )
      setCredentials({})
      setDraft(null)
      setAcknowledged(false)
      test.reset()
    },
  })
  const test = useMutation({
    mutationFn: () => ImSettingsService.settingsTestImConnection({ path }),
  })
  return (
    <form
      className="space-y-4"
      aria-label={platform.name}
      onSubmit={(event) => {
        event.preventDefault()
        if (suggestion?.blocked || (value.enabled && !acknowledged)) return
        save.mutate()
      }}
    >
      <fieldset
        disabled={save.isPending || suggestion?.blocked}
        className="min-w-0 space-y-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/40 p-3">
          <div>
            <p className="text-xs text-muted-foreground">
              {setting.has_credentials ? "凭据已设置" : "尚未设置凭据"}
              {setting.updated_at &&
                ` · 更新于 ${new Date(setting.updated_at).toLocaleString("zh-CN")}`}
            </p>
          </div>
          <DraftField changed={suggestion?.changed("enabled") ?? false}>
            <label
              className="flex items-center gap-2 text-sm"
              htmlFor={`${setting.platform}-enabled`}
            >
              <Checkbox
                id={`${setting.platform}-enabled`}
                checked={value.enabled}
                disabled={save.isPending}
                onCheckedChange={(checked) => {
                  setDraft({ ...value, enabled: checked === true })
                  setAcknowledged(false)
                  save.reset()
                }}
              />
              启用入口
            </label>
          </DraftField>
        </div>
        <p id="im-credentials-help" className="text-xs text-muted-foreground">
          在聊天平台的应用管理页面获取凭据。凭据加密保存，留空表示不修改。
        </p>
        <div className="grid gap-3">
          {Object.entries(platform.fields).map(([field, label]) => (
            <div key={field} className="space-y-2 text-sm">
              <label htmlFor={`${setting.platform}-${field}`}>{label}</label>
              <Input
                id={`${setting.platform}-${field}`}
                aria-describedby="im-credentials-help"
                type="password"
                autoComplete="new-password"
                maxLength={4096}
                value={credentials[field] ?? ""}
                placeholder="留空表示不改"
                disabled={save.isPending}
                onChange={(event) => {
                  setCredentials({
                    ...credentials,
                    [field]: event.target.value,
                  })
                  save.reset()
                  test.reset()
                }}
              />
            </div>
          ))}
        </div>
        {setting.platform !== "telegram" && (
          <DraftField changed={suggestion?.changed("email_auto_bind") ?? false}>
            <label
              className="flex items-center gap-2 text-sm"
              htmlFor={`${setting.platform}-email`}
            >
              <Checkbox
                id={`${setting.platform}-email`}
                checked={value.email_auto_bind ?? setting.email_auto_bind}
                disabled={save.isPending}
                onCheckedChange={(checked) => {
                  setDraft({ ...value, email_auto_bind: checked === true })
                  save.reset()
                }}
              />
              允许按企业邮箱自动绑定
            </label>
            <p className="text-sm text-muted-foreground">
              打开后，聊天账号的企业邮箱与唯一在职员工一致时自动绑定，无需输入绑定码。
            </p>
          </DraftField>
        )}
        {setting.platform === "wecom" && (
          <p className="text-sm text-muted-foreground">
            请使用企业微信的长连接模式，无需公网回调地址。同一应用只允许一个服务实例。
            无法读取企业邮箱时，请使用网页个人设置中的绑定码；新建应用通常无法读取邮箱。
            已启用时，连接测试读取最近心跳，不会另建连接。
          </p>
        )}
        {value.enabled && (
          <aside className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
            <p className="font-medium">数据外发提示</p>
            <p>IM 里的消息会存在该平台的服务器上</p>
            {setting.platform === "telegram" && (
              <p>服务器位于境外，境内网络可能无法直连。</p>
            )}
            <label
              className="flex items-center gap-2"
              htmlFor={`${setting.platform}-acknowledged`}
            >
              <Checkbox
                id={`${setting.platform}-acknowledged`}
                checked={acknowledged}
                disabled={save.isPending}
                onCheckedChange={(checked) => setAcknowledged(checked === true)}
              />
              已知晓数据外发提示
            </label>
          </aside>
        )}
        <SetupError error={save.error ?? test.error} />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            disabled={
              save.isPending ||
              test.isPending ||
              (value.enabled && !acknowledged)
            }
          >
            {save.isPending ? "正在保存…" : "保存"}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={
              !setting.has_credentials ||
              save.isPending ||
              test.isPending ||
              Object.values(credentials).some(Boolean)
            }
            onClick={() => test.mutate()}
          >
            {test.isPending
              ? "正在测试…"
              : setting.platform === "wecom" && setting.enabled
                ? "查看长连接最近心跳"
                : "测试已保存的连接"}
          </Button>
        </div>
        {save.isSuccess && <p role="status">设置已保存</p>}
        {test.data && (
          <p role={test.data.data.ok ? "status" : "alert"}>
            {test.data.data.message}
          </p>
        )}
      </fieldset>
    </form>
  )
}
