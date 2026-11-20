import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { SourcesService } from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { SetupError } from "@/components/Setup/SetupError"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

const statuses = {
  queued: "排队中",
  running: "同步中",
  succeeded: "已完成",
  failed: "失败",
}
const countLabels: Record<string, string> = {
  records: "记录",
  evidence_added: "新增证据",
  evidence_superseded: "更正证据",
  evidence_retracted: "撤销",
  people_unlinked: "未关联账号",
  tasks_added: "新增记录",
  tasks_updated: "更新记录",
  events_added: "新增事件",
  events_updated: "更新事件",
  evidence_unchanged: "未变更证据",
}
export function SyncRunTable({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [skip, setSkip] = useState(0)
  const runs = useQuery({
    queryKey: ["sync-runs", orgId, skip],
    queryFn: async () =>
      (
        await SourcesService.listSyncRuns({
          path: { org_id: orgId },
          query: { skip, limit: 20 },
        })
      ).data,
    refetchInterval: (q) =>
      q.state.data?.data.some(
        (r) => r.status === "queued" || r.status === "running",
      )
        ? 5000
        : false,
  })
  const retry = useMutation({
    mutationFn: (id: string) =>
      SourcesService.retrySync({ path: { org_id: orgId, run_id: id } }),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["sync-runs", orgId] }),
        client.invalidateQueries({ queryKey: ["source-runs", orgId] }),
      ]),
  })
  return (
    <SectionCard className="space-y-2 p-3" aria-label="同步记录">
      <h2 className="text-sm font-semibold">同步记录</h2>
      <SetupError error={runs.error ?? retry.error} />
      {runs.isError && <Button onClick={() => runs.refetch()}>重新读取</Button>}
      {runs.isPending && <p role="status">正在读取同步记录…</p>}
      {runs.data?.count === 0 && <p>暂无同步记录</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead>
            <tr className="h-10 border-b bg-muted/30 text-muted-foreground">
              {[
                "类型 / 文件名",
                "项目范围",
                "状态 / 尝试次数",
                "处理结果",
                "错误说明",
                "时间",
                "操作",
              ].map((h) => (
                <th
                  className="whitespace-nowrap px-2 py-1 text-xs font-medium"
                  key={h}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs.data?.data.map((run) => {
              const entries = Object.entries(run.counts)
              const overview = entries.filter(
                ([key]) => key === "records" || key === "evidence_added",
              )
              const additional = entries.filter(
                ([key]) => key !== "records" && key !== "evidence_added",
              )
              return (
                <tr key={run.id} className="h-11 border-b last:border-b-0">
                  <td className="px-2 py-1">
                    {run.kind === "jira" ? "Jira" : "CSV"}
                    {run.filename && (
                      <p className="break-all text-xs text-muted-foreground">
                        {run.filename}
                      </p>
                    )}
                  </td>
                  <td className="max-w-48 px-2 py-1 [overflow-wrap:anywhere]">
                    {run.kind === "jira"
                      ? run.project_keys?.length
                        ? run.project_keys.join("、")
                        : "全部项目"
                      : "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1">
                    <Badge
                      variant={
                        run.status === "failed" ? "destructive" : "secondary"
                      }
                    >
                      {statuses[run.status]}
                    </Badge>{" "}
                    <span className="text-xs text-muted-foreground">
                      {run.attempts} 次
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-xs">
                    <div className="flex gap-2">
                      {overview.map(([key, n]) => (
                        <span key={key}>
                          {countLabels[key]}：{n}
                        </span>
                      ))}
                    </div>
                    {additional.length > 0 && (
                      <details className="inline-block">
                        <summary className="cursor-pointer text-primary">
                          其他计数
                        </summary>
                        {additional.map(([key, n]) => (
                          <p key={key}>
                            {countLabels[key] ?? "其他"}：{n}
                          </p>
                        ))}
                      </details>
                    )}
                  </td>
                  <td className="max-w-48 px-2 py-1 text-xs">
                    {run.error && (
                      <details>
                        <summary className="cursor-pointer whitespace-nowrap text-destructive">
                          查看错误
                        </summary>
                        <p className="break-words">{run.error}</p>
                      </details>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-xs tabular-nums">
                    {new Date(run.created_at).toLocaleString("zh-CN")}
                    {run.finished_at && (
                      <p className="text-muted-foreground">
                        完成：
                        {new Date(run.finished_at).toLocaleString("zh-CN")}
                      </p>
                    )}
                  </td>
                  <td className="px-2 py-1">
                    {run.status === "failed" && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(run.id)}
                      >
                        重试
                      </Button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {!!runs.data?.count && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Button
            variant="outline"
            size="sm"
            disabled={skip === 0}
            onClick={() => setSkip(skip - 20)}
          >
            上一页
          </Button>
          <span>
            {skip + 1}–{Math.min(skip + 20, runs.data.count)} /{" "}
            {runs.data.count}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={skip + 20 >= runs.data.count}
            onClick={() => setSkip(skip + 20)}
          >
            下一页
          </Button>
        </div>
      )}
    </SectionCard>
  )
}
