import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { AccessTokensService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/assistants")({
  component: AssistantsPage,
  head: () => ({ meta: [{ title: "外部 AI 助手 - 员工画像平台" }] }),
})

function AssistantsPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <Settings key={org.id} orgId={org.id} />
}

function Settings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [clients, setClients] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const path = { org_id: orgId }
  const setting = useQuery({
    queryKey: ["mcp-setting", orgId],
    queryFn: async () =>
      (await AccessTokensService.readMcpSetting({ path })).data,
  })
  const save = useMutation({
    mutationFn: () =>
      AccessTokensService.updateMcpSetting({
        path,
        body: {
          enabled: enabled ?? setting.data?.enabled ?? false,
          allowed_clients: (
            clients ??
            setting.data?.allowed_clients?.join("，") ??
            ""
          )
            .split(/[,，]/)
            .map((name) => name.trim())
            .filter(Boolean),
        },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["mcp-setting", orgId], data)
      setEnabled(null)
      setClients(null)
      setMessage("已保存")
    },
    onError: handleError.bind(setMessage),
  })
  if (setting.isPending) return <ProfileLoading />
  if (setting.isError) return <ProfileError retry={() => setting.refetch()} />
  return (
    <div className="max-w-3xl space-y-6">
      <header className="border-l-4 border-primary pl-4">
        <h1 className="text-3xl font-semibold">外部 AI 助手</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          由员工授权外部助手查看本人的资料、提交画像请求。
        </p>
      </header>
      <p className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-5">
        打开后，员工的画像、证据和自述会发给外部助手的厂商。请先确认企业允许这些数据外发。
      </p>
      <form
        className="space-y-6 rounded-xl border p-6"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <label htmlFor="mcp-enabled" className="flex items-center gap-3">
          <Checkbox
            id="mcp-enabled"
            checked={enabled ?? setting.data.enabled}
            onCheckedChange={(value) => setEnabled(value === true)}
            disabled={save.isPending}
          />
          允许外部 AI 助手
        </label>
        <label htmlFor="mcp-clients" className="block space-y-2">
          允许的客户端
          <Input
            id="mcp-clients"
            value={clients ?? (setting.data.allowed_clients ?? []).join("，")}
            onChange={(event) => setClients(event.target.value)}
            placeholder="留空表示允许所有客户端，多个名称用逗号分隔"
            disabled={save.isPending}
          />
        </label>
        <p className="text-sm text-muted-foreground">
          按客户端自报的名称识别，不是安全边界，名称可以伪造。
        </p>
        <Button disabled={save.isPending}>
          {save.isPending ? "正在保存…" : "保存"}
        </Button>
        {message && <p role="status">{message}</p>}
      </form>
    </div>
  )
}
