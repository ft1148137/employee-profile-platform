import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { type BindingCode, ImBindingsService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

const names = { telegram: "Telegram", feishu: "飞书", wecom: "企业微信" }

export function ImBindings() {
  const { org, employeeId } = useOrg()
  if (!org) return <OrgState />
  if (!employeeId)
    return <p className="py-4">当前账号没有员工身份，不能绑定。</p>
  return <Bindings key={org.id} orgId={org.id} />
}

function Bindings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [code, setCode] = useState<BindingCode>()
  const [message, setMessage] = useState("")
  const path = { org_id: orgId }
  const queryKey = ["im-bindings", orgId]
  const bindings = useQuery({
    queryKey,
    queryFn: async () =>
      (await ImBindingsService.bindingsReadImBindings({ path })).data,
    refetchInterval: 5000,
  })
  const create = useMutation({
    mutationFn: async () =>
      (await ImBindingsService.bindingsCreateImBindCode({ path })).data,
    onSuccess: (data) => {
      setCode(data)
      setMessage("")
    },
    onError: handleError.bind(setMessage),
  })
  const remove = useMutation({
    mutationFn: (platform: keyof typeof names) =>
      ImBindingsService.bindingsDeleteImBinding({
        path: { ...path, platform },
      }),
    onSuccess: () => {
      setCode(undefined)
      setMessage("已解绑，旧绑定和绑定码已失效")
      client.invalidateQueries({ queryKey })
    },
    onError: handleError.bind(setMessage),
  })
  if (bindings.isPending) return <ProfileLoading />
  if (bindings.isError) return <ProfileError retry={bindings.refetch} />
  return (
    <section className="max-w-2xl space-y-6 py-4" aria-label="IM 绑定">
      <header className="space-y-2">
        <h2 className="text-xl font-semibold">绑定聊天账号</h2>
        <p className="text-sm text-muted-foreground">
          请先让管理员开启入口，再将一次性绑定码私聊发给企业机器人。请勿把绑定码交给他人。
        </p>
      </header>
      <div className="space-y-4 rounded-xl border bg-muted/20 p-5">
        <Button
          disabled={create.isPending}
          onClick={() => {
            setCode(undefined)
            create.mutate()
          }}
        >
          {code ? "重新生成绑定码" : "生成绑定码"}
        </Button>
        {code && (
          <div className="space-y-2" aria-live="polite">
            <p>在私聊中发送：</p>
            <p
              className="font-mono text-2xl tracking-widest"
              data-testid="im-bind-code"
            >
              绑定 {code.code}
            </p>
            <p className="text-sm text-muted-foreground">
              10 分钟内有效，仅可使用一次。到期时间：
              {new Date(code.expires_at).toLocaleTimeString("zh-CN")}。
              重新生成会使旧码失效。
            </p>
          </div>
        )}
      </div>
      <section className="space-y-3" aria-label="已绑定的账号">
        <h3 className="font-semibold">已绑定的账号</h3>
        {bindings.data.length === 0 && (
          <p className="text-sm text-muted-foreground">还没有绑定聊天账号</p>
        )}
        {bindings.data.map((row) => (
          <div
            key={row.platform}
            className="flex flex-wrap items-center justify-between gap-4 rounded-xl border p-5"
          >
            <div className="space-y-1">
              <p className="font-medium">
                {names[row.platform]} · {row.external_user_id}
              </p>
              <p className="text-sm text-muted-foreground">
                {row.active ? "已绑定" : "已失效，请确认入口已开启后重新绑定"}
              </p>
            </div>
            <Button
              variant="outline"
              disabled={remove.isPending}
              onClick={() => remove.mutate(row.platform)}
              aria-label={`解绑 ${names[row.platform]}`}
            >
              解绑
            </Button>
          </div>
        ))}
      </section>
      <p className="text-sm text-muted-foreground">
        改密码或更换企业机器人后需重新绑定。消息会存在对应平台服务器上，Telegram
        的服务器在境外。
      </p>
      {message && <p role="status">{message}</p>}
    </section>
  )
}
