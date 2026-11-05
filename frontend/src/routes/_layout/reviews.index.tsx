import { createFileRoute, Link } from "@tanstack/react-router"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { viaLabels } from "@/components/Profile/RequestTimeline"
import { useReviews } from "@/hooks/useReviews"

export const Route = createFileRoute("/_layout/reviews/")({
  component: ReviewsPage,
  head: () => ({ meta: [{ title: "待审批 - 员工画像平台" }] }),
})
function ReviewsPage() {
  const query = useReviews()
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="border-b pb-6">
        <h1 className="text-2xl font-semibold">待审批</h1>
        <p className="mt-2 text-muted-foreground">
          逐项核对员工的改动与依据，批准后才会发布画像。
        </p>
      </header>
      <p role="status">{query.data.count} 个请求待处理</p>
      <ul className="space-y-4">
        {query.data.data.map((request) => (
          <li key={request.id} className="rounded-xl border p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">
                {request.employee_name} ·{" "}
                {request.department_name ?? "未分配部门"}
              </h2>
              <span>{request.item_count} 项改动</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {request.submitted_at
                ? new Date(request.submitted_at).toLocaleString("zh-CN")
                : "—"}{" "}
              · {viaLabels[request.submitted_via ?? "web"]}
              {request.client_name && ` · ${request.client_name}`}
            </p>
            {!request.owner_reviewed && (
              <p className="text-sm text-amber-700">员工未过目</p>
            )}
            {request.can_decide ? (
              <Link
                className="inline-block text-primary underline"
                to="/reviews/$requestId"
                params={{ requestId: request.id }}
              >
                查看并审批
              </Link>
            ) : (
              <p role="status">{request.unavailable_reason}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
