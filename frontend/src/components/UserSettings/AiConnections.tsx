import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AccessTokensService, ProfileImportService } from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"
import { AuthorizedClients } from "./AuthorizedClients"

export function AiConnections() {
  const { org, employeeId } = useOrg()
  if (!org) return <OrgState />
  return <Tokens key={org.id} orgId={org.id} hasEmployee={!!employeeId} />
}

function Tokens({
  orgId,
  hasEmployee,
}: {
  orgId: string
  hasEmployee: boolean
}) {
  const client = useQueryClient()
  const [name, setName] = useState("")
  const [secret, setSecret] = useState("")
  const [message, setMessage] = useState("")
  const [configFeedback, setConfigFeedback] = useState("")
  const path = { org_id: orgId }
  const tokens = useQuery({
    queryKey: ["access-tokens", orgId],
    queryFn: async () =>
      (await AccessTokensService.readAccessTokens({ path })).data,
  })
  const setting = useQuery({
    queryKey: ["mcp-setting", orgId],
    queryFn: async () =>
      (await AccessTokensService.readMcpSetting({ path })).data,
  })
  const create = useMutation({
    mutationFn: async () => {
      const { data } = await AccessTokensService.createAccessToken({
        path,
        body: { name },
      })
      setSecret(data.token)
    },
    onSuccess: () => {
      setName("")
      setMessage("")
      client.invalidateQueries({ queryKey: ["access-tokens", orgId] })
    },
    onError: handleError.bind(setMessage),
  })
  const downloadSkill = useMutation({
    mutationFn: async () => {
      const { data } = await ProfileImportService.importDownloadImportFile({
        path: { name: "skill" },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("下载失败，请重试")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = "SKILL.md"
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    onError: handleError.bind(setMessage),
  })
  const revoke = useMutation({
    mutationFn: (id: string) =>
      AccessTokensService.revokeAccessToken({
        path: { ...path, token_id: id },
      }),
    onSuccess: () => {
      setSecret("")
      setMessage("令牌已吊销")
      client.invalidateQueries({ queryKey: ["access-tokens", orgId] })
    },
    onError: handleError.bind(setMessage),
  })
  const config = JSON.stringify(
    {
      mcpServers: {
        employeeProfile: {
          url: `${window.location.origin}/mcp`,
          headers: { Authorization: `Bearer ${secret || "<令牌>"}` },
        },
      },
    },
    null,
    2,
  )
  async function copy(text: string, setFeedback = setMessage) {
    try {
      await navigator.clipboard.writeText(text)
      setFeedback("已复制")
    } catch {
      setFeedback("复制失败，请手动选择并复制")
    }
  }
  if (tokens.isPending || setting.isPending) return <ProfileLoading />
  if (tokens.isError || setting.isError)
    return (
      <ProfileError
        retry={() => {
          tokens.refetch()
          setting.refetch()
        }}
      />
    )
  return (
    <SectionCard className="divide-y overflow-hidden p-0 text-sm">
      <header className="flex flex-wrap items-center justify-between gap-2 bg-muted/40 px-4 py-2.5">
        <h2 className="font-semibold">外部 AI 工具</h2>
        <Dialog
          onOpenChange={(open) => {
            if (open) setConfigFeedback("")
          }}
        >
          <DialogTrigger asChild>
            <Button size="sm" variant="outline" className="ml-auto">
              查看 MCP 接入配置
            </Button>
          </DialogTrigger>
          <DialogContent className="max-h-[calc(100dvh-2rem)] min-w-0 overflow-y-auto">
            <DialogHeader>
              <DialogTitle>查看 MCP 接入配置</DialogTitle>
              <DialogDescription>
                将配置粘贴到 Cursor 的 MCP
                设置中。若已隐藏令牌，请手动替换占位符。
              </DialogDescription>
            </DialogHeader>
            <pre
              className="min-w-0 overflow-x-auto rounded-md bg-muted p-3 text-xs"
              data-testid="cursor-config"
            >
              {config}
            </pre>
            {window.location.protocol === "http:" && (
              <p className="text-sm text-muted-foreground">
                当前为 HTTP 地址，外部工具连接需使用 HTTPS
                正式地址，请联系部署人员获取。
              </p>
            )}
            <div className="flex flex-wrap items-center justify-end gap-3">
              {configFeedback && (
                <p role="status" className="text-sm">
                  {configFeedback}
                </p>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => copy(config, setConfigFeedback)}
              >
                复制配置
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </header>
      {hasEmployee && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
          <p className="text-xs text-muted-foreground">
            使用外部 AI 生成画像文件后，可在「AI 助手」对话中上传导入。
          </p>
          <a
            href="/api/v1/profile-import/files/skill"
            className="ml-auto text-primary underline"
            onClick={(event) => {
              event.preventDefault()
              downloadSkill.mutate()
            }}
          >
            下载画像整理指南
          </a>
        </div>
      )}
      {!setting.data.enabled && (
        <p className="bg-muted/30 px-4 py-2.5 text-muted-foreground">
          企业未开启此功能，请联系管理员在「设置 → 外部 AI
          工具」中开启（已有令牌仍可查看或吊销）。
        </p>
      )}
      {!hasEmployee && (
        <p className="px-4 py-2.5 text-muted-foreground">
          当前账号没有员工身份，不能生成令牌。
        </p>
      )}
      <AuthorizedClients orgId={orgId} />
      <section aria-label="个人令牌与画像" className="space-y-2 px-4 py-2.5">
        <h3 className="font-medium">个人令牌与画像</h3>
        <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
          <li>
            读取本人画像、可见证据和画像变更请求。管辖经理也可查看管辖团队摘要。
          </li>
          <li>
            直接提交本人的画像变更请求，系统会记录提交来源和客户端。需审批的改动可在经理批准前撤回。
          </li>
          <li>
            普通员工不能审批，任何人都不能审批自己的请求。有审批权的经理可凭令牌通过
            MCP 在管辖范围内提交画像审批决定。
          </li>
          <li>
            工具指引要求先征得经理本人同意。
            <strong>平台不强制逐次验证经理是否同意。</strong>
            每次调用都会留下审计记录，记录来源和客户端。
          </li>
          <li>协作、负荷、延期审批与方案接受仅限网页操作。</li>
        </ul>
        <p className="text-muted-foreground">
          工具以你的身份操作，权限受你本人账号的限制。可随时在本页「我的令牌」中吊销。
        </p>
      </section>
      <form
        className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5"
        onSubmit={(event) => {
          event.preventDefault()
          setSecret("")
          create.mutate()
        }}
      >
        <div>
          <label htmlFor="token-name" className="font-medium">
            令牌名称
          </label>
          <p id="token-hint" className="text-xs text-muted-foreground">
            请妥善保管令牌，默认 90 天有效。
          </p>
        </div>
        <div className="ml-auto flex w-full items-center gap-2 sm:w-auto">
          <Input
            id="token-name"
            className="h-8 min-w-0 flex-1 sm:w-44"
            aria-describedby="token-hint"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={100}
            required
            placeholder="例如：我的工作电脑"
          />
          <Button
            size="sm"
            disabled={
              !setting.data.enabled ||
              !hasEmployee ||
              !name.trim() ||
              create.isPending
            }
          >
            生成令牌
          </Button>
        </div>
      </form>
      {secret && (
        <section
          className="space-y-2 bg-muted/30 px-4 py-3"
          aria-label="新令牌"
        >
          <h3 className="font-semibold">令牌仅显示一次，请立即复制保存</h3>
          <code className="block break-all" data-testid="new-token">
            {secret}
          </code>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => copy(secret)}>
              复制令牌
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSecret("")}>
              已保存，隐藏令牌
            </Button>
          </div>
        </section>
      )}
      <section className="divide-y" aria-label="我的令牌">
        {tokens.data.length === 0 && (
          <p className="px-4 py-2.5 text-muted-foreground">还没有生成令牌</p>
        )}
        {tokens.data.map((token) => (
          <fieldset
            key={token.id}
            aria-label={token.name}
            className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <p className="min-w-0 wrap-anywhere font-medium">
                  {token.name}
                </p>
                <code className="text-xs text-muted-foreground">
                  {token.prefix}…
                </code>
                <Badge variant="outline">
                  {token.revoked_at
                    ? "已吊销"
                    : Date.parse(token.expires_at) <= Date.now()
                      ? "已过期"
                      : "有效"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                到期：{new Date(token.expires_at).toLocaleDateString("zh-CN")} ·
                最近使用：
                {token.last_used_at
                  ? new Date(token.last_used_at).toLocaleString("zh-CN")
                  : "尚未使用"}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={!!token.revoked_at || revoke.isPending}
              onClick={() => revoke.mutate(token.id)}
            >
              吊销
            </Button>
          </fieldset>
        ))}
      </section>
      {message && (
        <p role="status" className="px-4 py-2.5">
          {message}
        </p>
      )}
    </SectionCard>
  )
}
