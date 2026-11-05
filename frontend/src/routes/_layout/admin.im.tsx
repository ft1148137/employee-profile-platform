import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import {
  type ImSettingPublic,
  ImSettingsService,
  type ImSettingUpdate,
} from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/im")({
  component: Page,
  head: () => ({ meta: [{ title: "IM 入口 - 员工画像平台" }] }),
})

const platforms = {
  feishu: {
    name: "飞书",
    fields: { app_id: "应用标识", app_secret: "应用密钥" },
  },
  wecom: {
    name: "企业微信",
    fields: {
      bot_id: "机器人标识",
      secret: "机器人密钥",
      corp_id: "企业标识（选填）",
      contact_secret: "通讯录密钥（选填）",
    },
  },
  telegram: { name: "Telegram", fields: { bot_token: "机器人令牌" } },
}

function Page() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <Settings key={org.id} orgId={org.id} />
}

function Settings({ orgId }: { orgId: string }) {
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
    <div className="max-w-3xl space-y-6">
      <header className="border-l-4 border-primary pl-4">
        <p className="text-xs text-muted-foreground">管理 · 员工沟通渠道</p>
        <h1 className="mt-2 text-3xl font-semibold">IM 入口</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          分别管理各平台的入口与应用凭据。凭据加密保存，保存后不再显示；留空表示不改。
        </p>
      </header>
      {settings.data.map((setting) => (
        <PlatformSetting
          key={setting.platform}
          orgId={orgId}
          setting={setting}
        />
      ))}
    </div>
  )
}

function PlatformSetting({
  orgId,
  setting,
}: {
  orgId: string
  setting: ImSettingPublic
}) {
  const client = useQueryClient()
  const platform = platforms[setting.platform]
  const path = { org_id: orgId, platform: setting.platform }
  const [draft, setDraft] = useState<ImSettingUpdate | null>(null)
  const value = draft ?? setting
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
      className="space-y-5 rounded-xl border p-6"
      aria-label={platform.name}
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{platform.name}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {setting.has_credentials ? "凭据已设置" : "尚未设置凭据"}
            {setting.updated_at &&
              ` · 更新于 ${new Date(setting.updated_at).toLocaleString("zh-CN")}`}
          </p>
        </div>
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
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {Object.entries(platform.fields).map(([field, label]) => (
          <label
            key={field}
            className="space-y-2 text-sm"
            htmlFor={`${setting.platform}-${field}`}
          >
            <span>{label}</span>
            <Input
              id={`${setting.platform}-${field}`}
              type="password"
              autoComplete="new-password"
              maxLength={4096}
              value={credentials[field] ?? ""}
              placeholder="留空表示不改"
              disabled={save.isPending}
              onChange={(event) => {
                setCredentials({ ...credentials, [field]: event.target.value })
                save.reset()
                test.reset()
              }}
            />
          </label>
        ))}
      </div>
      {setting.platform !== "telegram" && (
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
      )}
      {setting.platform === "wecom" && (
        <p className="text-sm text-muted-foreground">
          请使用智能机器人的长连接模式，无需公网回调地址。同一机器人只允许一个服务实例。
          无法读取企业邮箱时，请使用网页个人设置中的绑定码；新建应用通常无法读取邮箱。
          已启用时，连接测试读取最近心跳，不会另建连接。
        </p>
      )}
      {value.enabled && (
        <aside className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
          <p className="font-medium">数据外发提示</p>
          <p>IM 里的消息会存在该平台的服务器上</p>
          {setting.platform === "telegram" && (
            <p>服务器在境外，中国大陆通常连不上</p>
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
            save.isPending || test.isPending || (value.enabled && !acknowledged)
          }
        >
          {save.isPending ? "正在保存…" : "保存"}
        </Button>
        {setting.platform !== "feishu" ? (
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
        ) : (
          <p className="text-sm text-muted-foreground">
            连接测试将在后续接入完成后可用
          </p>
        )}
      </div>
      {save.isSuccess && <p role="status">设置已保存</p>}
      {test.data && (
        <p role={test.data.data.ok ? "status" : "alert"}>
          {test.data.data.message}
        </p>
      )}
    </form>
  )
}
