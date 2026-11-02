import { createFileRoute } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { WizardFrame } from "@/components/Setup/WizardFrame"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/setup-wizard")({
  component: SetupWizard,
  head: () => ({ meta: [{ title: "首次设置 - 员工画像平台" }] }),
})

function SetupWizard() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">只有企业管理员可以进行首次设置。</p>
  return <WizardFrame key={org.id} orgId={org.id} />
}
