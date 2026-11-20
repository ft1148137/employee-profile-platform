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
  head: () => ({ meta: [{ title: "应用授权 - 员工画像平台" }] }),
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
    return <p role="alert">授权链接无效或已过期，请从外部工具重新发起连接。</p>
  return (
    <section className="mx-auto max-w-xl space-y-6 rounded-2xl border bg-card p-6 md:p-10">
      <ShieldCheck className="size-10 text-primary" aria-hidden="true" />
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">外部工具授权</p>
        <h1 className="break-words text-2xl font-semibold">
          {info.data.client_name} 申请访问你的账号
        </h1>
        <p className="text-xs text-muted-foreground">
          应用名称由对方自行提供，请核实来源。
        </p>
      </header>
      <div className="space-y-2 border-y py-4 text-sm">
        <p>
          当前企业：<strong>{info.data.org_name}</strong>
        </p>
        <p className="text-muted-foreground">
          如需授权其他企业，请先在左侧切换企业。
        </p>
        <p className="break-all">授权后跳转至：{info.data.callback_host}</p>
      </div>
      <div>
        <h2 className="mb-3 font-medium">授权后，该工具可以：</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm">
          <li>查看你的画像、相关记录、能力词典和申请进度</li>
          <li>起草、提交或撤回画像更新申请</li>
          <li>代你提交待确认问题的回答</li>
        </ul>
      </div>
      <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
        你的画像及相关记录将发送给该工具及其 AI
        服务商；它提交的更新申请，在经理审批前你都可以撤回。
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
        可随时在「设置 → 连接与集成」中撤销授权。
      </p>
    </section>
  )
}
