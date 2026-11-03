import { useQuery } from "@tanstack/react-query"
import { SourcesService, type SyncRunPublic } from "@/client"

export function useSourceRuns(orgId: string) {
  return useQuery({
    queryKey: ["source-runs", orgId],
    queryFn: async () =>
      (await SourcesService.listSyncRuns({ path: { org_id: orgId } })).data
        .data,
    refetchInterval: (query) =>
      query.state.data?.some(
        (run) => run.status === "queued" || run.status === "running",
      )
        ? 3000
        : false,
  })
}

const statuses = {
  queued: "等待中",
  running: "进行中",
  succeeded: "成功",
  failed: "失败",
}
const counts: Record<string, string> = {
  records: "记录",
  tasks_added: "新增任务",
  tasks_updated: "更新任务",
  evidence_added: "新增证据",
  evidence_superseded: "更正证据",
  evidence_retracted: "撤销证据",
  people_unlinked: "未关联账号",
  evidence_unchanged: "未变证据",
  events_updated: "更新工作事件",
  events_added: "新增工作事件",
}

export function LastSyncStatus({ run }: { run?: SyncRunPublic }) {
  if (!run)
    return <p className="text-sm text-muted-foreground">还没有同步记录</p>
  return (
    <div
      role="status"
      className="space-y-2 rounded-lg border bg-muted/30 p-4 text-sm"
    >
      <p className="font-medium">
        {run.kind === "jira" ? "Jira 同步" : "文件导入"}：{statuses[run.status]}
      </p>
      <p className="break-all text-muted-foreground">
        {run.filename} ·{" "}
        {new Date(
          run.finished_at ?? run.started_at ?? run.created_at,
        ).toLocaleString("zh-CN")}
      </p>
      <p>
        {Object.entries(run.counts)
          .map(([key, value]) => `${counts[key] ?? "其他计数"} ${value}`)
          .join(" · ")}
      </p>
      {run.error && <p className="text-destructive">{run.error}</p>}
    </div>
  )
}
