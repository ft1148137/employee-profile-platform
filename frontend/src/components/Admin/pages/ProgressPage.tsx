import { TodayPanel } from "@/components/Admin/TodayPanel"
import { OrgState } from "@/components/Profile/OrgState"
import { DraftProgress } from "@/components/Progress/DraftProgress"
import { SyncRunTable } from "@/components/Progress/SyncRunTable"
import { useOrg } from "@/hooks/useOrg"
export function ProgressPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <div key={org.id} className="max-w-6xl space-y-8">
      <TodayPanel orgId={org.id} />
      <header className="border-l-4 border-primary pl-4">
        <p className="text-xs tracking-widest text-muted-foreground">管理</p>
        <h1 className="mt-2 text-3xl font-semibold">同步与起草进度</h1>
      </header>
      <SyncRunTable orgId={org.id} />
      <DraftProgress orgId={org.id} />
    </div>
  )
}
