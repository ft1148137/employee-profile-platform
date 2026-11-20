import { ModelSettingForm } from "@/components/Model/ModelSettingForm"
export function ModelStep({ orgId }: { orgId: string }) {
  return (
    <div className="space-y-4">
      <p>自动起草需先配置并启用 AI 模型。</p>
      <ModelSettingForm orgId={orgId} />
    </div>
  )
}
