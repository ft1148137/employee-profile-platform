import type { ComponentType } from "react"
import { AccountsStep } from "./AccountsStep"
import { CapabilitiesStep } from "./CapabilitiesStep"
import { IdentitiesStep } from "./IdentitiesStep"
import { ModelStep } from "./ModelStep"
import { OrgProfileStep } from "./OrgProfileStep"
import { RosterStep } from "./RosterStep"
import { SourcesStep } from "./SourcesStep"

export type StepProps = { orgId: string; onComplete: () => void }

export const setupSteps: Record<string, ComponentType<StepProps>> = {
  org_profile: OrgProfileStep,
  roster: RosterStep,
  capabilities: CapabilitiesStep,
  accounts: AccountsStep,
  sources: SourcesStep,
  identities: IdentitiesStep,
  model: ModelStep,
}
