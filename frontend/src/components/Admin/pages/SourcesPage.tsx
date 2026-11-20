import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import { OrgState } from "@/components/Profile/OrgState"
import { SyncRunTable } from "@/components/Progress/SyncRunTable"
import { SourceForms } from "@/components/Sources/SourceForms"
import { useOrg } from "@/hooks/useOrg"

export function SourcesPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">你没有权限管理数据来源，只有企业管理员可以操作。</p>
  return (
    <div className="space-y-4">
      <h1 className="sr-only">数据来源</h1>
      <TopbarActions>
        <ChangeHistory targetType="source_connection" variant="drawer" />
      </TopbarActions>
      <SourceForms key={org.id} orgId={org.id} />
      <div id="sync-runs">
        <SyncRunTable key={org.id} orgId={org.id} />
      </div>
    </div>
  )
}
