import type { RequestReviewer } from "@/client"

export function RequestReviewers({
  reviewers = [],
  preview = false,
}: {
  reviewers?: RequestReviewer[]
  preview?: boolean
}) {
  const names = reviewers.map((r) => `${r.name}（${r.role}）`).join("、")
  return (
    <p>
      {names
        ? preview
          ? `提交后由 ${names}审批`
          : `待经理审批：${names}`
        : "暂时没有可用的审批人，企业管理员会处理"}
    </p>
  )
}
