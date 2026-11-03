import type { ComponentType } from "react"
import { AccountsStep } from "./AccountsStep"
import { CapabilitiesStep } from "./CapabilitiesStep"
import { InitialProfilesStep } from "./InitialProfilesStep"
import { OrgProfileStep } from "./OrgProfileStep"
import { RosterStep } from "./RosterStep"

export type StepProps = { orgId: string; onComplete: () => void }

export const setupSteps: Record<string, ComponentType<StepProps>> = {
  org_profile: OrgProfileStep,
  roster: RosterStep,
  capabilities: CapabilitiesStep,
  accounts: AccountsStep,
  initial_profiles: InitialProfilesStep,
}
