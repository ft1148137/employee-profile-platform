import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { OrgState } from "@/components/Profile/OrgState"
import { SourcesStep } from "@/components/Setup/steps/SourcesStep"
import { useOrg } from "@/hooks/useOrg"

export function SourcesPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">你没有权限管理数据来源，只有企业管理员可以操作。</p>
  return (
    <div className="space-y-6">
      <div className="border-l-4 border-primary pl-4">
        <p className="mb-1 text-xs tracking-widest text-muted-foreground">
          管理 · 工作记录
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">数据来源</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          连接 Jira 或上传文件，为员工画像补充事实依据。
        </p>
      </div>
      <SourcesStep key={org.id} orgId={org.id} onComplete={() => {}} />
      <ChangeHistory targetType="source_connection" />
    </div>
  )
}
