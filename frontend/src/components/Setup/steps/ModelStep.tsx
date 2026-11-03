import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
import type { StepProps } from "."
export function ModelStep({ orgId }: StepProps) {
  return <ModelSettingForm orgId={orgId} />
}
