import { IdentityTable } from "@/components/Identities/IdentityTable"
import type { StepProps } from "."

export function IdentitiesStep({ orgId }: StepProps) {
  return <IdentityTable orgId={orgId} inWizard />
}
