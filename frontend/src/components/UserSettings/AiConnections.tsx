import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AccessTokensService, ProfileImportService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
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
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setMessage("已复制")
    } catch {
      setMessage("复制失败，请手动选择并复制")
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
    <section className="max-w-3xl space-y-6 py-4">
      <header>
        <h2 className="text-xl font-semibold">连接 AI 助手</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          个人令牌可让外部助手访问你的画像和证据，并以你的身份提交请求。请像密码一样保管，默认
          90 天到期。
        </p>
      </header>
      {hasEmployee && (
        <div className="space-y-2">
          <a
            href="/api/v1/profile-import/files/skill"
            className="text-primary underline"
            onClick={(event) => {
              event.preventDefault()
              downloadSkill.mutate()
            }}
          >
            下载生成指南（skill）
          </a>
          <p className="text-sm text-muted-foreground">
            外部 AI 按指南生成的文件，请在助手对话里上传
          </p>
        </div>
      )}
      {!setting.data.enabled && (
        <p className="rounded-lg border bg-muted/30 p-4">
          企业管理员关闭了这个功能。需要使用的话，请联系企业管理员在「数据与 AI
          → 外部 AI 助手」开启。你仍可查看和吊销已有令牌。
        </p>
      )}
      {!hasEmployee && <p>当前账号没有员工身份，不能生成令牌。</p>}
      <AuthorizedClients orgId={orgId} />
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          setSecret("")
          create.mutate()
        }}
      >
        <label htmlFor="token-name" className="flex-1 min-w-48 space-y-2">
          令牌名称
          <Input
            id="token-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={100}
            required
            placeholder="例如：我的工作电脑"
          />
        </label>
        <Button
          disabled={
            !setting.data.enabled ||
            !hasEmployee ||
            !name.trim() ||
            create.isPending
          }
        >
          生成令牌
        </Button>
      </form>
      {secret && (
        <section
          className="space-y-3 rounded-xl border border-primary/40 bg-muted/20 p-5"
          aria-label="新令牌"
        >
          <h3 className="font-semibold">请立即保存，明文只显示这一次</h3>
          <code className="block break-all" data-testid="new-token">
            {secret}
          </code>
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => copy(secret)}>
              复制令牌
            </Button>
            <Button variant="ghost" onClick={() => setSecret("")}>
              已保存，隐藏令牌
            </Button>
          </div>
        </section>
      )}
      <section className="space-y-3" aria-label="我的令牌">
        <h3 className="font-semibold">我的令牌</h3>
        {tokens.data.length === 0 && (
          <p className="text-sm text-muted-foreground">还没有生成令牌</p>
        )}
        {tokens.data.map((token) => (
          <fieldset
            key={token.id}
            aria-label={token.name}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4"
          >
            <div>
              <p className="font-medium">{token.name}</p>
              <p className="text-sm text-muted-foreground">
                <code>{token.prefix}…</code> ·{" "}
                {token.revoked_at
                  ? "已吊销"
                  : Date.parse(token.expires_at) <= Date.now()
                    ? "已过期"
                    : "有效"}
              </p>
              <p className="text-xs text-muted-foreground">
                到期：{new Date(token.expires_at).toLocaleDateString("zh-CN")} ·
                最近使用：
                {token.last_used_at
                  ? new Date(token.last_used_at).toLocaleString("zh-CN")
                  : "尚未使用"}
              </p>
            </div>
            <Button
              variant="outline"
              disabled={!!token.revoked_at || revoke.isPending}
              onClick={() => revoke.mutate(token.id)}
            >
              吊销
            </Button>
          </fieldset>
        ))}
      </section>
      <section className="space-y-3 rounded-xl border p-5">
        <h3 className="font-semibold">Cursor 配置示例</h3>
        <p className="text-sm text-muted-foreground">
          需要外部客户端能访问到的 HTTPS 地址。请在 Cursor 的 MCP
          设置中添加以下配置；隐藏明文后，请自行替换「令牌」。
        </p>
        <pre
          className="overflow-x-auto rounded-lg bg-muted p-4 text-xs"
          data-testid="cursor-config"
        >
          {config}
        </pre>
        {window.location.protocol === "http:" && (
          <p className="text-sm text-muted-foreground">
            你现在是用 HTTP 地址打开的，只适合在部署平台的这台电脑上测试；外部
            AI 客户端需要 HTTPS 地址，请向部署平台的人要正式网址。
          </p>
        )}
        <Button variant="outline" onClick={() => copy(config)}>
          复制配置
        </Button>
      </section>
      {message && <p role="status">{message}</p>}
    </section>
  )
}
