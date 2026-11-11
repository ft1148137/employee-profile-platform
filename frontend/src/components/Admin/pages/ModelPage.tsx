import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"
import { DraftingPage } from "./DraftingPage"
export function ModelPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <div key={org.id} className="max-w-6xl space-y-8">
      <header className="border-l-4 border-primary pl-4">
        <p className="text-xs tracking-widest text-muted-foreground">管理</p>
        <h1 className="mt-2 text-3xl font-semibold">模型</h1>
      </header>
      <DraftingPage orgId={org.id} />
      <ModelSettingForm orgId={org.id} />
      <ChangeHistory targetType="model_setting" />
      <ChangeHistory targetType="drafting_setting" />
    </div>
  )
}
