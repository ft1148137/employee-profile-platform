import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import {
  type JiraConfig,
  type JiraConnectionPublic,
  SourcesService,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LastSyncStatus, useSourceRuns } from "./LastSyncStatus"

export function JiraSourceForm({ orgId }: { orgId: string }) {
  const connection = useQuery({
    queryKey: ["jira", orgId],
    queryFn: async () =>
      (await SourcesService.getJira({ path: { org_id: orgId } })).data,
  })
  if (connection.isPending) return <p role="status">正在加载 Jira 配置…</p>
  if (connection.isError)
    return (
      <>
        <SetupError error={connection.error} />
        <Button onClick={() => connection.refetch()}>重试</Button>
      </>
    )
  return <JiraForm key={orgId} orgId={orgId} connection={connection.data} />
}

function JiraForm({
  orgId,
  connection,
}: {
  orgId: string
  connection: JiraConnectionPublic
}) {
  const client = useQueryClient()
  const start = new Date()
  const year = start.getFullYear() - 1
  const day = Math.min(
    start.getDate(),
    new Date(year, start.getMonth() + 1, 0).getDate(),
  )
  const [config, setConfig] = useState<JiraConfig>(
    connection.config ?? {
      deployment: "cloud",
      base_url: "",
      auth_type: "api_token",
      username: "",
      start_date: `${year}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
      include_description: false,
    },
  )
  const [secret, setSecret] = useState("")
  const [hasSecret, setHasSecret] = useState(connection.has_secret)
  const [dirty, setDirty] = useState(false)
  const runs = useSourceRuns(orgId, "jira")
  const latest = runs.data?.[0]
  const syncing = latest?.status === "queued" || latest?.status === "running"
  const save = useMutation({
    mutationFn: () =>
      SourcesService.putJira({
        path: { org_id: orgId },
        body: { ...config, secret: secret || undefined },
      }),
    onSuccess: ({ data }) => {
      setSecret("")
      setHasSecret(data.has_secret)
      setDirty(false)
      client.setQueryData(["jira", orgId], data)
      test.reset()
    },
  })
  const test = useMutation({
    mutationFn: () => SourcesService.testJira({ path: { org_id: orgId } }),
  })
  const sync = useMutation({
    mutationFn: () => SourcesService.startJiraSync({ path: { org_id: orgId } }),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["source-runs", orgId] }),
  })
  function change(patch: Partial<JiraConfig>) {
    setConfig({ ...config, ...patch })
    setDirty(true)
    save.reset()
    test.reset()
  }
  const busy = save.isPending || test.isPending || sync.isPending
  return (
    <section
      aria-label="Jira 连接"
      className="space-y-4 rounded-xl border p-4 sm:p-6"
    >
      <h3 className="text-lg font-semibold">连接 Jira</h3>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <fieldset disabled={busy || syncing} className="space-y-4">
          <legend className="mb-2 text-sm font-medium">Jira 类型</legend>
          <div className="flex flex-wrap gap-4">
            {(["cloud", "data_center"] as const).map((type) => (
              <label key={type} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="deployment"
                  checked={config.deployment === type}
                  onChange={() =>
                    change({
                      deployment: type,
                      auth_type: type === "cloud" ? "api_token" : "pat",
                      username: "",
                    })
                  }
                />
                {type === "cloud" ? "Jira Cloud" : "Jira Data Center / Server"}
              </label>
            ))}
          </div>
          <div className="space-y-2">
            <Label htmlFor="jira-url">站点地址</Label>
            <Input
              id="jira-url"
              type="url"
              required
              maxLength={500}
              placeholder="https://your-company.atlassian.net"
              value={config.base_url}
              onChange={(e) => change({ base_url: e.target.value })}
            />
          </div>
          {config.deployment === "data_center" && (
            <div className="space-y-2">
              <Label htmlFor="jira-auth">认证方式</Label>
              <select
                id="jira-auth"
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={config.auth_type}
                onChange={(e) =>
                  change({
                    auth_type: e.target.value === "pat" ? "pat" : "basic",
                  })
                }
              >
                <option value="pat">个人访问令牌（Jira 8.14 及以上）</option>
                <option value="basic">用户名和密码（更老的版本）</option>
              </select>
            </div>
          )}
          {config.auth_type !== "pat" && (
            <div className="space-y-2">
              <Label htmlFor="jira-user">
                {config.deployment === "cloud" ? "账号邮箱" : "用户名"}
              </Label>
              <Input
                id="jira-user"
                type={config.deployment === "cloud" ? "email" : "text"}
                required
                maxLength={255}
                value={config.username ?? ""}
                onChange={(e) => change({ username: e.target.value })}
              />
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="jira-secret">
              {config.auth_type === "basic"
                ? "密码"
                : config.auth_type === "pat"
                  ? "个人访问令牌"
                  : "API token"}
            </Label>
            <Input
              id="jira-secret"
              type="password"
              autoComplete="new-password"
              required={!hasSecret}
              placeholder={hasSecret ? "已保存，留空表示不修改" : "请输入凭据"}
              value={secret}
              onChange={(e) => {
                setSecret(e.target.value)
                setDirty(true)
                save.reset()
                test.reset()
              }}
            />
            {config.deployment === "cloud" && (
              <p className="text-xs text-muted-foreground">
                在 Atlassian 账号设置的「安全」中创建 API token，再填写到这里。
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="jira-start">首次同步的起始日期</Label>
            <Input
              id="jira-start"
              type="date"
              required
              value={config.start_date}
              onChange={(e) => change({ start_date: e.target.value })}
            />
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={config.include_description}
              onChange={(e) =>
                change({ include_description: e.target.checked })
              }
            />
            <span>
              同步任务描述（description）
              <span className="mt-1 block text-xs text-muted-foreground">
                勾选后，任务描述会存进平台，并可能在起草时发给模型
              </span>
            </span>
          </label>
        </fieldset>
        <p className="text-xs text-muted-foreground">
          修改后请先保存，再测试连接或立即同步。
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || syncing}>
            保存
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy || dirty || !hasSecret}
            onClick={() => test.mutate()}
          >
            测试连接
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={
              busy ||
              dirty ||
              !hasSecret ||
              syncing ||
              runs.isPending ||
              runs.isError
            }
            onClick={() => sync.mutate()}
          >
            立即同步
          </Button>
        </div>
      </form>
      <SetupError
        error={save.error ?? test.error ?? sync.error ?? runs.error}
      />
      {save.isSuccess && <p role="status">Jira 配置已保存。</p>}
      {test.data && (
        <div role={test.data.data.ok ? "status" : "alert"} className="text-sm">
          <p>
            {test.data.data.message}
            {test.data.data.account_name && `：${test.data.data.account_name}`}
          </p>
          {!test.data.data.ok && (
            <p>如果暂时接不上，可以先在下方上传工作记录文件</p>
          )}
        </div>
      )}
      <LastSyncStatus run={latest} />
      <a href="/admin/progress" className="text-sm text-primary underline">
        查看同步与起草进度
      </a>
    </section>
  )
}
