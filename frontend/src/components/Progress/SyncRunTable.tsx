import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { SourcesService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
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
  evidence_superseded: "取代",
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
      client.invalidateQueries({ queryKey: ["sync-runs", orgId] }),
  })
  return (
    <section className="space-y-4" aria-label="同步记录">
      <h2 className="text-xl font-semibold">同步记录</h2>
      <SetupError error={runs.error ?? retry.error} />
      {runs.isError && <Button onClick={() => runs.refetch()}>重新读取</Button>}
      {runs.isPending && <p role="status">正在读取同步记录…</p>}
      {runs.data?.count === 0 && <p>还没有同步记录</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead>
            <tr className="border-b">
              {[
                "类型 / 文件名",
                "状态 / 尝试次数",
                "计数",
                "错误说明",
                "时间",
                "操作",
              ].map((h) => (
                <th className="p-3 whitespace-nowrap" key={h}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs.data?.data.map((run) => (
              <tr key={run.id} className="border-b align-top">
                <td className="p-3">
                  {run.kind === "jira" ? "Jira" : "CSV"}
                  <p>{run.filename}</p>
                  {run.kind === "jira" && (
                    <p>
                      项目范围：
                      {run.project_keys?.length
                        ? run.project_keys.join("、")
                        : "全部项目"}
                    </p>
                  )}
                </td>
                <td className="p-3 whitespace-nowrap">
                  {statuses[run.status]}
                  <p>{run.attempts} 次</p>
                </td>
                <td className="p-3">
                  {Object.entries(run.counts).map(([key, n]) => (
                    <p className="whitespace-nowrap" key={key}>
                      {countLabels[key] ?? "其他"}：{n}
                    </p>
                  ))}
                </td>
                <td className="p-3">{run.error ?? "—"}</td>
                <td className="p-3">
                  {new Date(run.created_at).toLocaleString("zh-CN")}
                  <p>
                    {run.finished_at &&
                      `完成：${new Date(run.finished_at).toLocaleString("zh-CN")}`}
                  </p>
                </td>
                <td className="p-3">
                  {run.status === "failed" && (
                    <Button
                      variant="outline"
                      disabled={retry.isPending}
                      onClick={() => retry.mutate(run.id)}
                    >
                      重试
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!!runs.data?.count && (
        <div className="flex gap-3 items-center">
          <Button
            variant="outline"
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
            disabled={skip + 20 >= runs.data.count}
            onClick={() => setSkip(skip + 20)}
          >
            下一页
          </Button>
        </div>
      )}
    </section>
  )
}
