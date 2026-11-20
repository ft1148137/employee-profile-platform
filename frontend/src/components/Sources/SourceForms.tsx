import { useQuery } from "@tanstack/react-query"
import { Cloud, FileSpreadsheet, Server } from "lucide-react"
import { SourcesService, type SyncRunPublic } from "@/client"
import { IntegrationCard } from "@/components/Admin/pages/IntegrationCard"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { CsvImportCard } from "@/components/Sources/CsvImportCard"
import { JiraSourceForm } from "@/components/Sources/JiraSourceForm"
import { useSourceRuns } from "./LastSyncStatus"

const jiraTypes = {
  cloud: {
    name: "Jira Cloud",
    Icon: Cloud,
    color: "bg-blue-500/10 text-blue-600 dark:text-blue-300",
    description: "从 Atlassian 云站点同步任务和工作记录。",
  },
  data_center: {
    name: "Jira Data Center",
    Icon: Server,
    color: "bg-slate-500/10 text-slate-600 dark:text-slate-300",
    description: "从自托管 Jira 同步任务，支持个人访问令牌或账号密码。",
  },
}

function runTime(run: SyncRunPublic) {
  return new Date(
    run.finished_at ?? run.started_at ?? run.created_at,
  ).toLocaleString("zh-CN")
}

export function SourceForms({ orgId }: { orgId: string }) {
  const assistant = usePageDraft(orgId, "update_jira_connection")
  const connection = useQuery({
    queryKey: ["jira", orgId],
    queryFn: async () =>
      (await SourcesService.getJira({ path: { org_id: orgId } })).data,
  })
  const jiraRuns = useSourceRuns(orgId, "jira")
  const csvRuns = useSourceRuns(orgId, "csv")
  const savedDeployment = connection.data?.config?.deployment
  const previewDeployment = assistant.values({
    deployment: savedDeployment ?? "cloud",
  }).deployment
  const jiraLatest = jiraRuns.data?.[0]
  const csvLatest = csvRuns.data?.[0]
  return (
    <div className="space-y-3">
      <PageDraft
        orgId={orgId}
        draft={assistant.draft}
        error={assistant.error}
      />
      <p className="text-sm text-muted-foreground">
        每家企业可配置一个 Jira 连接，也可上传 CSV 或 Excel 工作记录。
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(["cloud", "data_center"] as const).map((deployment) => {
          const { name, Icon, color, description } = jiraTypes[deployment]
          const configured =
            savedDeployment === deployment && !!connection.data?.has_secret
          const suggested = !!assistant.item && previewDeployment === deployment
          return (
            <IntegrationCard
              key={deployment}
              name={name}
              icon={
                <Icon
                  aria-hidden="true"
                  className={`size-8 rounded-lg p-2 ${color}`}
                />
              }
              description={description}
              status={
                connection.isPending
                  ? "读取中"
                  : connection.isError
                    ? "读取失败"
                    : configured
                      ? "已配置"
                      : "未配置"
              }
              active={configured}
              detail={
                configured && jiraLatest
                  ? `最近 Jira 同步：${runTime(jiraLatest)}`
                  : configured
                    ? "暂无同步记录"
                    : "尚未设置凭据"
              }
              suggested={suggested}
              disabled={!!assistant.item && !suggested}
            >
              {assistant.item && !suggested ? (
                <p className="text-sm">
                  请关闭抽屉，打开标有助手草稿的配置查看建议。
                </p>
              ) : connection.isPending ? (
                <ProfileLoading />
              ) : connection.isError ? (
                <ProfileError retry={() => connection.refetch()} />
              ) : (
                <JiraSourceForm
                  key={assistant.draft?.id}
                  orgId={orgId}
                  connection={connection.data}
                  deployment={deployment}
                  assistant={assistant}
                />
              )}
            </IntegrationCard>
          )
        })}
        <IntegrationCard
          name="CSV / Excel 工作记录"
          icon={
            <FileSpreadsheet
              aria-hidden="true"
              className="size-8 rounded-lg bg-emerald-500/10 p-2 text-emerald-700 dark:text-emerald-300"
            />
          }
          description="上传 CSV 或 Excel 文件，将其他系统的工作记录导入画像平台。"
          status={csvLatest ? "已有导入记录" : "按需导入"}
          active={csvLatest?.status === "succeeded"}
          detail={
            csvLatest ? `最近导入：${runTime(csvLatest)}` : "暂无导入记录"
          }
          action="导入"
        >
          <CsvImportCard orgId={orgId} />
        </IntegrationCard>
      </div>
    </div>
  )
}
