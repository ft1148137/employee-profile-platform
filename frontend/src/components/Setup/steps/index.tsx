import type { ComponentType } from "react"
import { CapabilitiesStep } from "./CapabilitiesStep"
import { OrgProfileStep } from "./OrgProfileStep"

export type StepProps = { orgId: string; onComplete: () => void }
const Placeholder = () => (
  <p className="rounded-lg bg-muted p-4 text-sm">
    此步骤的网页操作将在后续版本接入。你可以先查看其他步骤。
  </p>
)

export const setupSteps: Record<string, ComponentType<StepProps>> = {
  org_profile: OrgProfileStep,
  roster: Placeholder,
  capabilities: CapabilitiesStep,
  accounts: Placeholder,
}
