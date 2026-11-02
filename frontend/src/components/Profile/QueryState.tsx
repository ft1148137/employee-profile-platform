import ErrorComponent from "@/components/Common/ErrorComponent"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function ProfileLoading() {
  return (
    <div role="status" aria-label="正在加载" className="space-y-4 py-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-40 w-full" />
    </div>
  )
}

export function ProfileError({ retry }: { retry: () => void }) {
  return (
    <div role="alert" className="text-center [&>div]:min-h-0">
      <ErrorComponent />
      <Button variant="outline" onClick={retry}>
        重试
      </Button>
    </div>
  )
}
