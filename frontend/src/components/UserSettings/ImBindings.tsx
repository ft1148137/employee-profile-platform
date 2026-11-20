import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { type BindingCode, ImBindingsService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

const names = { telegram: "Telegram", feishu: "飞书", wecom: "企业微信" }

export function ImBindings() {
  const { org, employeeId } = useOrg()
  if (!org) return <OrgState />
  if (!employeeId)
    return (
      <p className="px-4 py-3 text-sm text-muted-foreground">
        当前账号没有员工身份，不能绑定。
      </p>
    )
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
  const notification = useMutation({
    mutationFn: ({
      platform,
      notify,
    }: {
      platform: keyof typeof names
      notify: boolean
    }) =>
      ImBindingsService.bindingsUpdateImBindingNotify({
        path: { ...path, platform },
        body: { notify },
      }),
    onSuccess: () => {
      setMessage("推送设置已保存")
      client.invalidateQueries({ queryKey })
    },
    onError: handleError.bind(setMessage),
  })
  if (bindings.isPending) return <ProfileLoading />
  if (bindings.isError) return <ProfileError retry={bindings.refetch} />
  return (
    <section aria-label="IM 绑定">
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
        <div>
          <h3 className="text-sm font-medium">绑定聊天账号</h3>
          <p className="text-xs text-muted-foreground">
            将绑定码私聊发给企业助手，请勿泄露给他人。
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={create.isPending}
          onClick={() => {
            setCode(undefined)
            create.mutate()
          }}
        >
          {code ? "重新生成绑定码" : "生成绑定码"}
        </Button>
      </header>
      {code && (
        <div
          className="mx-4 mb-3 space-y-1 rounded-md border bg-muted/30 p-3 text-sm"
          aria-live="polite"
        >
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
      <section className="divide-y border-t" aria-label="已绑定的账号">
        {bindings.data.length === 0 && (
          <p className="px-4 py-2.5 text-sm text-muted-foreground">
            暂未绑定聊天账号
          </p>
        )}
        {bindings.data.map((row) => (
          <div
            key={row.platform}
            className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <p className="min-w-0 wrap-anywhere font-medium">
                  {names[row.platform]} · {row.external_user_id}
                </p>
                <Badge
                  variant="outline"
                  className={
                    row.active
                      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : "text-muted-foreground"
                  }
                >
                  {row.active ? "已绑定" : "已失效"}
                </Badge>
              </div>
              {!row.active && (
                <p className="text-xs text-muted-foreground">
                  已失效，请确认入口已开启后重新绑定
                </p>
              )}
            </div>
            <div className="ml-auto flex flex-wrap items-center justify-end gap-3">
              <label
                className="flex items-center gap-2 text-sm"
                htmlFor={`notify-${row.platform}`}
              >
                <Checkbox
                  id={`notify-${row.platform}`}
                  checked={row.notify}
                  disabled={notification.isPending}
                  onCheckedChange={(checked) =>
                    notification.mutate({
                      platform: row.platform,
                      notify: checked === true,
                    })
                  }
                  aria-label={`${names[row.platform]} 推送通知`}
                />
                推送通知
              </label>
              <Button
                size="sm"
                variant="outline"
                disabled={remove.isPending}
                onClick={() => remove.mutate(row.platform)}
                aria-label={`解绑 ${names[row.platform]}`}
              >
                解绑
              </Button>
            </div>
          </div>
        ))}
      </section>
      <p className="border-t px-4 py-2.5 text-xs text-muted-foreground">
        修改密码或更换企业应用后需重新绑定。聊天消息将留存于对应平台服务器（Telegram
        服务器位于境外）。
      </p>
      {message && (
        <p className="px-4 pb-2.5 text-sm" role="status">
          {message}
        </p>
      )}
    </section>
  )
}
