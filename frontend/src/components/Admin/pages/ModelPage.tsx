import { useQuery } from "@tanstack/react-query"
import { ModelSettingService } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import {
  SectionCard,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
import { OrgState } from "@/components/Profile/OrgState"
import { DraftProgress } from "@/components/Progress/DraftProgress"
import { Badge } from "@/components/ui/badge"
import { useOrg } from "@/hooks/useOrg"
import { DraftingPage } from "./DraftingPage"

export function ModelPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <WorkspacePage key={org.id} variant="form">
      <h1 className="sr-only">模型</h1>
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,65fr)_minmax(0,35fr)]">
        <div className="min-w-0 space-y-4">
          <SectionCard aria-labelledby="model-configuration">
            <h2 id="model-configuration" className="mb-3 text-sm font-semibold">
              模型配置
            </h2>
            <ModelSettingForm orgId={org.id} />
          </SectionCard>
          <DraftingPage orgId={org.id} />
        </div>
        <aside className="min-w-0 space-y-4">
          <ModelStatus orgId={org.id} />
          <div id="draft-progress" className="scroll-mt-4">
            <DraftProgress orgId={org.id} />
          </div>
          <SectionCard aria-labelledby="model-history">
            <h2 id="model-history" className="text-sm font-semibold">
              配置变更记录
            </h2>
            <div className="mt-2 divide-y divide-surface-border">
              {[
                ["model_setting", "模型配置"],
                ["drafting_setting", "自动起草"],
                ["drafting_note", "起草补充说明"],
              ].map(([targetType, label]) => (
                <section
                  key={targetType}
                  aria-label={`${label}历史`}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 last:pb-0"
                >
                  <span className="text-sm">{label}</span>
                  <ChangeHistory targetType={targetType} variant="drawer" />
                </section>
              ))}
            </div>
          </SectionCard>
        </aside>
      </div>
    </WorkspacePage>
  )
}

function ModelStatus({ orgId }: { orgId: string }) {
  const model = useQuery({
    queryKey: ["model-setting", orgId],
    queryFn: async () =>
      (
        await ModelSettingService.settingGetModelSetting({
          path: { org_id: orgId },
        })
      ).data,
  })
  const saved = model.data
  return (
    <SectionCard aria-labelledby="model-status" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id="model-status" className="text-sm font-semibold">
          运行状态
        </h2>
        {saved && (
          <Badge variant={saved.enabled ? "default" : "secondary"}>
            {saved.enabled ? "已启用" : "未启用"}
          </Badge>
        )}
      </div>
      {model.isPending && (
        <p role="status" className="text-sm">
          正在读取运行状态…
        </p>
      )}
      {model.isError && (
        <p className="text-sm text-muted-foreground">暂时无法读取运行状态。</p>
      )}
      {saved?.enabled && (
        <dl className="space-y-2 text-sm">
          <div className="space-y-1">
            <dt className="text-muted-foreground">生效模型</dt>
            <dd className="break-all font-medium">{saved.model || "—"}</dd>
          </div>
          {saved.kind === "chatgpt_subscription" && (
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <dt className="text-muted-foreground">ChatGPT 订阅</dt>
              <dd>
                {saved.chatgpt?.needs_relogin
                  ? "需要重新连接"
                  : saved.chatgpt?.connected
                    ? "已连接"
                    : "未连接"}
              </dd>
            </div>
          )}
        </dl>
      )}
    </SectionCard>
  )
}
