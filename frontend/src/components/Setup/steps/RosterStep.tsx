import { RosterImport } from "@/components/Roster/RosterImport"
import type { StepProps } from "."

export function RosterStep({ orgId }: StepProps) {
  return <RosterImport orgId={orgId} />
}
