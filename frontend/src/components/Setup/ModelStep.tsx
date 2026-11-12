import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
export function ModelStep({ orgId }: { orgId: string }) {
  return (
    <div className="space-y-4">
      <p>起草需要 AI 模型，请配置并启用模型后继续。</p>
      <ModelSettingForm orgId={orgId} />
    </div>
  )
}
