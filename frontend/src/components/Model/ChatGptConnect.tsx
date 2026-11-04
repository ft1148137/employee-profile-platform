import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { ModelSettingService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function ChatGptConnect({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const [redirect, setRedirect] = useState("")
  const [now, setNow] = useState(Date.now())
  const status = useQuery({
    queryKey: ["chatgpt", orgId],
    queryFn: async () =>
      (await ModelSettingService.settingPollChatgptLogin({ path })).data,
    refetchInterval: (q) => (q.state.data?.status === "pending" ? 5000 : false),
  })
  useEffect(() => {
    if (status.data?.status !== "pending") return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [status.data?.status])
  const start = useMutation({
    mutationFn: () => ModelSettingService.settingStartChatgptLogin({ path }),
    onSuccess: ({ data }) => client.setQueryData(["chatgpt", orgId], data),
  })
  const complete = useMutation({
    mutationFn: () =>
      ModelSettingService.settingCompleteChatgptLogin({
        path,
        body: { input: redirect },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["chatgpt", orgId], data)
      setRedirect("")
      client.invalidateQueries({ queryKey: ["model-setting", orgId] })
    },
  })
  const cancel = useMutation({
    mutationFn: () => ModelSettingService.settingCancelChatgptLogin({ path }),
    onSuccess: ({ data }) => {
      client.setQueryData(["chatgpt", orgId], data)
      setRedirect("")
    },
  })
  const disconnect = useMutation({
    mutationFn: () => ModelSettingService.settingDisconnectChatgpt({ path }),
    onSuccess: () => {
      client.setQueryData(["chatgpt", orgId], { status: "none" })
      client.invalidateQueries({ queryKey: ["model-setting", orgId] })
    },
  })
  const data = status.data
  const remaining = data?.expires_at
    ? Math.max(0, Math.ceil((Date.parse(data.expires_at) - now) / 1000))
    : 0
  return (
    <section
      className="space-y-4 rounded-lg border bg-muted/20 p-4"
      aria-label="ChatGPT 连接"
    >
      <SetupError
        error={
          status.error ??
          start.error ??
          complete.error ??
          cancel.error ??
          disconnect.error
        }
      />
      {data?.account?.needs_relogin && (
        <p role="alert">登录已失效，请重新连接</p>
      )}
      {data?.status === "connected" && !data.account?.needs_relogin ? (
        <>
          <p role="status">
            已连接 ChatGPT：{data.account?.email ?? data.account?.account_id}{" "}
            {data.account?.plan_type}
          </p>
          <p>
            断开后令牌在 OpenAI 那边不会失效；要立即作废，请在 ChatGPT
            的安全设置里退出这个会话。
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={disconnect.isPending}
            onClick={() => disconnect.mutate()}
          >
            断开连接
          </Button>
        </>
      ) : data?.status === "pending" && remaining > 0 ? (
        <>
          <p>
            打开授权链接并登录，将浏览器最后跳转的完整网址粘贴到下方。无法打开
            localhost 页面是正常现象。
          </p>
          <a
            className="break-all underline"
            href={data.verification_url ?? undefined}
            target="_blank"
            rel="noreferrer"
          >
            打开 ChatGPT 授权页面
          </a>
          <label className="block">
            重定向网址
            <input
              className="w-full rounded border p-2"
              value={redirect}
              onChange={(e) => setRedirect(e.target.value)}
              autoComplete="off"
            />
          </label>
          <Button
            type="button"
            disabled={!redirect || complete.isPending}
            onClick={() => complete.mutate()}
          >
            完成连接
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={cancel.isPending || complete.isPending}
            onClick={() => cancel.mutate()}
          >
            取消连接
          </Button>
          <p role="status">剩余 {Math.ceil(remaining / 60)} 分钟</p>
        </>
      ) : (
        <>
          {data?.message && <p role="alert">{data.message}</p>}
          {(data?.status === "expired" || data?.status === "pending") && (
            <p>授权链接已过期，请重新开始。</p>
          )}
          <Button
            type="button"
            disabled={start.isPending}
            onClick={() => start.mutate()}
          >
            连接 ChatGPT
          </Button>
        </>
      )}

      <ul className="list-disc space-y-2 pl-5 text-sm text-muted-foreground">
        <li>
          这种方式走的是 ChatGPT 的 Codex 后端，不是 OpenAI
          面向第三方应用的官方计划，OpenAI 以后可能限制。
        </li>
        <li>
          订阅有 5
          小时和每周两种用量上限。只配订阅时，全企业员工和内置机器人的对话共用这一个账号的额度。到上限时起草会暂停，额度恢复后自动继续。
        </li>
        <li>
          个人 Plus/Pro 适用 ChatGPT 个人版的数据设置，内容可能被用于训练，请在
          ChatGPT 的「数据控制」里关闭。
        </li>
        <li>
          处理真实员工数据，建议用 Business/Enterprise 工作区，或者改用 API。
        </li>
      </ul>
    </section>
  )
}
