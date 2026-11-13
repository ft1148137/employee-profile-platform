import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import {
  type JiraConfig,
  type JiraConnectionPublic,
  type JiraProject,
  SourcesService,
} from "@/client"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
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
  const [manualConfig, setConfig] = useState<JiraConfig>(
    connection.config ?? {
      deployment: "cloud",
      base_url: "",
      auth_type: "api_token",
      username: "",
      start_date: `${year}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
      include_description: false,
    },
  )
  const [schedule, setSchedule] = useState(connection.sync_schedule ?? "off")
  const [secret, setSecret] = useState("")
  const [hasSecret, setHasSecret] = useState(connection.has_secret)
  const [dirty, setDirty] = useState(false)
  const [projects, setProjects] = useState<JiraProject[]>([])
  const assistant = usePageDraft(
    orgId,
    "update_jira_connection",
    useCallback(() => {
      if (connection.config) setConfig(connection.config)
      setSchedule(connection.sync_schedule ?? "off")
      setSecret("")
      setHasSecret(connection.has_secret)
      setDirty(false)
    }, [connection]),
  )
  const config = assistant.values(manualConfig)
  const displayedSchedule = assistant.values({
    sync_schedule: schedule,
  }).sync_schedule
  const runs = useSourceRuns(orgId, "jira")
  const latest = runs.data?.[0]
  const syncing = latest?.status === "queued" || latest?.status === "running"
  const save = useMutation({
    mutationFn: () =>
      SourcesService.putJira({
        path: { org_id: orgId },
        body: {
          ...config,
          project_keys: config.project_keys?.filter(Boolean),
          sync_schedule: schedule,
          secret: secret || undefined,
        },
      }),
    onSuccess: ({ data }) => {
      if (data.config) setConfig(data.config)
      setSecret("")
      setHasSecret(data.has_secret)
      setDirty(false)
      client.setQueryData(["jira", orgId], data)
      test.reset()
    },
  })
  const test = useMutation({
    mutationFn: () => SourcesService.testJira({ path: { org_id: orgId } }),
    onSuccess: ({ data }) => setProjects(data.ok ? (data.projects ?? []) : []),
  })
  const sync = useMutation({
    mutationFn: () => SourcesService.startJiraSync({ path: { org_id: orgId } }),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["source-runs", orgId] }),
        client.invalidateQueries({ queryKey: ["sync-runs", orgId] }),
      ]),
  })
  function change(patch: Partial<JiraConfig>) {
    if (
      patch.base_url !== undefined ||
      patch.deployment !== undefined ||
      patch.username !== undefined ||
      patch.auth_type !== undefined
    )
      setProjects([])
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
      <PageDraft
        orgId={orgId}
        draft={assistant.draft}
        error={assistant.error}
      />
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <fieldset
          disabled={busy || syncing || assistant.blocked}
          className="space-y-4"
        >
          <legend className="mb-2 text-sm font-medium">Jira 类型</legend>
          <DraftField changed={assistant.changed("deployment")}>
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
                  {type === "cloud"
                    ? "Jira Cloud"
                    : "Jira Data Center / Server"}
                </label>
              ))}
            </div>
          </DraftField>
          <p className="text-xs text-muted-foreground">
            按企业正在使用的 Jira
            版本选择，云端版与企业自行部署的版本需要不同的连接方式。
          </p>
          <DraftField changed={assistant.changed("base_url")}>
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
            <p className="text-xs text-muted-foreground">
              填写企业 Jira 首页的网址，用于读取已授权的工作记录。
            </p>
          </DraftField>
          {config.deployment === "data_center" && (
            <DraftField changed={assistant.changed("auth_type")}>
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
            </DraftField>
          )}
          {config.auth_type !== "pat" && (
            <DraftField changed={assistant.changed("username")}>
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
            </DraftField>
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
                setProjects([])
                setDirty(true)
                save.reset()
                test.reset()
              }}
            />
            <p className="text-xs text-muted-foreground">
              这是授权平台读取工作记录的连接凭据，加密保存；已有凭据时留空表示不修改。
            </p>
            {config.deployment === "cloud" && (
              <p className="text-xs text-muted-foreground">
                在 Atlassian 账号设置的「安全」中创建 API
                token（连接密钥），再填写到这里。
              </p>
            )}
          </div>
          <DraftField changed={assistant.changed("start_date")}>
            <Label htmlFor="jira-start">首次同步的起始日期</Label>
            <Input
              id="jira-start"
              type="date"
              required
              value={config.start_date}
              onChange={(e) => change({ start_date: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              首次同步只读取这个日期之后的工作记录，默认从一年前开始。
            </p>
          </DraftField>
          <DraftField changed={assistant.changed("project_keys")}>
            <Label htmlFor="jira-projects">同步项目标识</Label>
            <Input
              id="jira-projects"
              value={(config.project_keys ?? []).join(",")}
              placeholder="例如 QG,AB；留空同步全部项目"
              aria-describedby="jira-projects-help"
              onChange={(e) =>
                change({
                  project_keys: e.target.value
                    .split(/[,，]/)
                    .map((key) => key.trim()),
                })
              }
            />
            <p
              id="jira-projects-help"
              className="text-xs text-muted-foreground"
            >
              可直接填写项目标识（逗号分隔，最多 50
              个），或保存后测试连接并勾选。缩小范围不会删除已经同步的记录。
            </p>
            {projects.length > 0 && (
              <fieldset className="mt-2 max-h-48 space-y-2 overflow-y-auto rounded-md border p-3">
                <legend className="px-1 text-sm">
                  可见项目（最多显示 100 个）
                </legend>
                {projects.map((project) => (
                  <label
                    key={project.key}
                    className="flex items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={(config.project_keys ?? []).some(
                        (key) => key.toUpperCase() === project.key,
                      )}
                      onChange={(e) =>
                        change({
                          project_keys: e.target.checked
                            ? [
                                ...(config.project_keys ?? []).filter(Boolean),
                                project.key,
                              ]
                            : (config.project_keys ?? []).filter(
                                (key) => key.toUpperCase() !== project.key,
                              ),
                        })
                      }
                    />
                    {project.key}（{project.name}）
                  </label>
                ))}
              </fieldset>
            )}
          </DraftField>
          <DraftField changed={assistant.changed("sync_schedule")}>
            <Label htmlFor="jira-schedule">同步周期</Label>
            <select
              id="jira-schedule"
              className="h-9 w-full rounded-md border bg-background px-2 text-sm"
              value={displayedSchedule}
              onChange={(e) => {
                const value = e.target.value
                if (
                  value === "off" ||
                  value === "monthly" ||
                  value === "quarterly" ||
                  value === "yearly"
                ) {
                  setSchedule(value)
                  setDirty(true)
                  save.reset()
                }
              }}
            >
              <option value="off">关闭</option>
              <option value="monthly">每月</option>
              <option value="quarterly">每季度</option>
              <option value="yearly">每年</option>
            </select>
            <p className="text-xs text-muted-foreground">
              开启周期后按时补充工作记录；关闭时仍可点击「立即同步」。
            </p>
          </DraftField>
          <DraftField changed={assistant.changed("include_description")}>
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
          </DraftField>
        </fieldset>
        <p className="text-xs text-muted-foreground">
          修改后请先保存，再测试连接或立即同步。
        </p>
        <p className="text-sm font-medium">
          将同步：
          {config.project_keys?.filter(Boolean).length
            ? config.project_keys
                .filter(Boolean)
                .map((key) => {
                  const project = projects.find(
                    (p) => p.key === key.toUpperCase(),
                  )
                  return project
                    ? `${project.key}（${project.name}）`
                    : key.toUpperCase()
                })
                .join("、")
            : "全部项目"}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || syncing || assistant.blocked}>
            保存
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy || dirty || !hasSecret || assistant.blocked}
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
              runs.isError ||
              assistant.blocked
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
      <p className="text-sm text-muted-foreground">
        下次同步时间：
        {connection.next_sync_at
          ? new Date(connection.next_sync_at).toLocaleString("zh-CN")
          : "定时同步已关闭"}
      </p>
      <LastSyncStatus run={latest} />
      <a
        href="/admin/data-ai?tab=sources#sync-runs"
        className="text-sm text-primary underline"
      >
        查看同步记录
      </a>
    </section>
  )
}
