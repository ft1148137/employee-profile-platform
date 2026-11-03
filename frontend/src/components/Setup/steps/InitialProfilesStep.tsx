import { DraftProgress } from "@/components/Progress/DraftProgress"
import type { StepProps } from "."
export function InitialProfilesStep({ orgId }: StepProps) {
  return <DraftProgress orgId={orgId} wizard />
}
