import { Building2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export function OrgState() {
  const { isPending, isError, refetch } = useOrg()
  if (isPending) return <p role="status">正在加载企业信息…</p>
  if (isError)
    return (
      <div role="alert">
        加载失败，请重试
        <Button variant="outline" onClick={() => refetch()}>
          重试
        </Button>
      </div>
    )
  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-4 rounded-xl border border-dashed bg-muted/20 text-center">
      <Building2 className="size-10 text-muted-foreground" />
      <h1 className="text-xl font-semibold">
        你的账号尚未加入任何企业，请联系企业管理员。
      </h1>
    </div>
  )
}
