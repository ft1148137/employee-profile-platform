import type { RequestReviewer } from "@/client"

export function RequestReviewers({
  reviewers = [],
  preview = false,
  requiresManager = true,
  partialAutoPublish = false,
}: {
  reviewers?: RequestReviewer[]
  preview?: boolean
  requiresManager?: boolean
  partialAutoPublish?: boolean
}) {
  if (preview && !requiresManager) return null
  const names = reviewers.map((r) => `${r.name}（${r.role}）`).join("、")
  return (
    <p>
      {preview && partialAutoPublish && "需经理确认的改动："}
      {names
        ? preview
          ? `提交后由 ${names}审批`
          : `待经理审批：${names}`
        : "暂时没有可用的审批人，企业管理员会处理"}
    </p>
  )
}
