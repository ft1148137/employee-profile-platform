import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { ShieldCheck } from "lucide-react"
import { useState } from "react"
import { OauthService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/oauth/consent")({
  validateSearch: (search: Record<string, unknown>) => ({
    request: typeof search.request === "string" ? search.request : "",
  }),
  component: ConsentPage,
  head: () => ({ meta: [{ title: "授权 AI 助手 - 员工画像平台" }] }),
})

function ConsentPage() {
  const { org, isPending } = useOrg()
  const { request } = Route.useSearch()
  if (isPending) return <ProfileLoading />
  if (!org) return <OrgState />
  return <Consent key={org.id + request} orgId={org.id} request={request} />
}

function Consent({ orgId, request }: { orgId: string; request: string }) {
  const [message, setMessage] = useState("")
  const query = { org_id: orgId, request }
  const info = useQuery({
    queryKey: ["oauth-consent", orgId, request],
    queryFn: async () => (await OauthService.readConsent({ query })).data,
    retry: false,
  })
  const decision = useMutation({
    mutationFn: (approve: boolean) =>
      OauthService.consent({
        query: { org_id: orgId },
        body: { request, approve },
      }),
    onSuccess: ({ data }) => window.location.assign(data.redirect_to),
    onError: handleError.bind(setMessage),
  })
  if (info.isPending) return <ProfileLoading />
  if (info.isError)
    return (
      <p role="alert">
        授权请求无效、已过期或暂时无法读取，请从客户端重新连接。
      </p>
    )
  return (
    <section className="mx-auto max-w-xl space-y-6 rounded-2xl border bg-card p-6 md:p-10">
      <ShieldCheck className="size-10 text-primary" aria-hidden="true" />
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">连接 AI 助手 · 授权确认</p>
        <h1 className="break-words text-2xl font-semibold">
          {info.data.client_name} 想以你的身份查看和更新你的画像
        </h1>
        <p className="text-xs text-muted-foreground">名称由客户端自己填写</p>
      </header>
      <div className="space-y-2 border-y py-4 text-sm">
        <p>
          将以 <strong>{info.data.org_name}</strong> 的员工身份授权
        </p>
        <p className="text-muted-foreground">
          如需使用其他企业，请先通过侧栏切换企业。
        </p>
        <p className="break-all">授权后返回：{info.data.callback_host}</p>
      </div>
      <div>
        <h2 className="mb-3 font-medium">这个助手将能</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm">
          <li>查看你的画像、证据、能力词典和请求进度</li>
          <li>起草、提交和撤回你的画像变更请求</li>
          <li>回答待确认问题、提出申诉</li>
        </ul>
      </div>
      <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
        画像和证据内容会发给这个客户端和它背后的 AI
        服务。助手能直接提交请求，经理批准前你可以撤回。
      </p>
      {info.data.reason && <p role="alert">{info.data.reason}</p>}
      {message && <p role="alert">{message}</p>}
      <div className="flex gap-3">
        {!info.data.reason && (
          <Button
            disabled={decision.isPending}
            onClick={() => decision.mutate(true)}
          >
            允许
          </Button>
        )}
        {!info.data.reason && (
          <Button
            variant="outline"
            disabled={decision.isPending}
            onClick={() => decision.mutate(false)}
          >
            拒绝
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        你可以随时在「个人设置 → 连接 AI 助手」中撤销授权。
      </p>
    </section>
  )
}
