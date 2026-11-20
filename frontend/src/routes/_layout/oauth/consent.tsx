import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  ArrowLeftRight,
  Check,
  Plug,
  TriangleAlert,
  UsersRound,
} from "lucide-react"
import { useState } from "react"
import { OauthService } from "@/client"
import {
  SectionCard,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
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
  return (
    <WorkspacePage className="grid min-h-[calc(100dvh-3rem)] place-items-center py-6">
      <SectionCard className="w-full max-w-md space-y-4 rounded-xl p-5 shadow-md shadow-black/5 sm:p-6">
        {isPending ? (
          <ProfileLoading />
        ) : org ? (
          <Consent key={org.id + request} orgId={org.id} request={request} />
        ) : (
          <OrgState />
        )}
      </SectionCard>
    </WorkspacePage>
  )
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
    <>
      <header className="space-y-2 text-center">
        <div
          className="mb-3 flex items-center justify-center gap-3"
          aria-hidden="true"
        >
          <span className="grid size-10 place-items-center rounded-lg border border-violet-500/25 bg-violet-500/10 text-violet-700 dark:text-violet-300">
            <Plug className="size-5" />
          </span>
          <ArrowLeftRight className="size-4 text-muted-foreground" />
          <span className="grid size-10 place-items-center rounded-lg bg-primary text-primary-foreground">
            <UsersRound className="size-5" />
          </span>
        </div>
        <h1 className="wrap-anywhere text-xl font-semibold">
          {info.data.client_name} 申请访问你的账号
        </h1>
        <p className="text-xs text-muted-foreground">
          应用名称由对方自行提供，请核实来源。
        </p>
      </header>
      <div className="space-y-1 border-y py-3 text-sm">
        <p className="wrap-anywhere">
          当前企业：<strong>{info.data.org_name}</strong>
        </p>
        <p className="wrap-anywhere">授权后跳转至：{info.data.callback_host}</p>
        <p className="text-xs text-muted-foreground">
          如需授权其他企业，请先在侧栏切换企业。
        </p>
      </div>
      <div className="space-y-2 rounded-md border border-surface-border bg-canvas p-3 text-sm">
        <h2 className="font-medium" id="oauth-permissions">
          授权后，该工具可以
        </h2>
        <ul className="space-y-1.5" aria-labelledby="oauth-permissions">
          {[
            "查看你的画像、相关记录、能力词典和申请进度",
            "起草、提交或撤回画像更新申请",
            "代你提交待确认问题的回答",
          ].map((permission) => (
            <li key={permission} className="flex items-start gap-2">
              <Check
                className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-300"
                aria-hidden="true"
              />
              {permission}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>
          <strong>数据外发提醒。</strong>你的画像及相关记录将发送给该工具及其 AI
          服务商。它提交的更新申请，在经理审批前你都可以撤回。
        </p>
      </div>
      {info.data.reason && <p role="alert">{info.data.reason}</p>}
      {message && <p role="alert">{message}</p>}
      {!info.data.reason && (
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="outline"
            disabled={decision.isPending}
            onClick={() => decision.mutate(false)}
          >
            拒绝
          </Button>
          <Button
            disabled={decision.isPending}
            onClick={() => decision.mutate(true)}
          >
            允许
          </Button>
        </div>
      )}
      <p className="text-center text-xs text-muted-foreground">
        可随时在「设置 → 连接与集成」中撤销授权。
      </p>
    </>
  )
}
