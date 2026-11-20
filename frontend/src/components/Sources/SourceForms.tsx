import { CsvImportCard } from "@/components/Sources/CsvImportCard"
import { JiraSourceForm } from "@/components/Sources/JiraSourceForm"

export function SourceForms({ orgId }: { orgId: string }) {
  return (
    <div className="space-y-5">
      <JiraSourceForm orgId={orgId} />
      <CsvImportCard orgId={orgId} />
      <p className="text-sm text-muted-foreground">
        暂不接入也可继续，后续可随时在「设置 → 数据来源」中配置。
      </p>
    </div>
  )
}
