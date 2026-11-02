import { Building2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export function OrgState() {
  const { isPending, isError, refetch } = useOrg()
  if (isPending) return <p role="status">正在加载组织…</p>
  if (isError)
    return (
      <div role="alert">
        无法加载组织。
        <Button variant="outline" onClick={() => refetch()}>
          重试
        </Button>
      </div>
    )
  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-4 rounded-xl border border-dashed bg-muted/20 text-center">
      <Building2 className="size-10 text-muted-foreground" />
      <h1 className="text-xl font-semibold">
        你还没有加入任何组织，请联系管理员
      </h1>
    </div>
  )
}
