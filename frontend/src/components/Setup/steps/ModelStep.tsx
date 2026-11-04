import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
import type { StepProps } from "."
export function ModelStep({ orgId }: StepProps) {
  return (
    <div className="space-y-4">
      <p>起草需要 AI 模型，请配置并启用模型后继续。</p>
      <ModelSettingForm orgId={orgId} />
    </div>
  )
}
