import { CsvImportCard } from "@/components/Sources/CsvImportCard"
import { JiraSourceForm } from "@/components/Sources/JiraSourceForm"

export function SourceForms({ orgId }: { orgId: string }) {
  return (
    <div className="space-y-5">
      <JiraSourceForm orgId={orgId} />
      <CsvImportCard orgId={orgId} />
      <p className="text-sm text-muted-foreground">
        没有工作记录也能继续，初始画像里的能力都会是「未知」。以后可以在「数据来源」页接入。
      </p>
    </div>
  )
}
