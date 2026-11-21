import { equal } from "node:assert/strict"
import { test } from "node:test"
import { previewHistoryLabel, requestCardHistory } from "./requestCards"

const preview = {
  type: "preview_card",
  request_id: "request",
  revision: 2,
  items: [],
  actions: [
    { action: "confirm_submit", request_id: "request", revision: 2 } as const,
  ],
} as const

test("request history matches only cards with the same request ID", () => {
  const active = { ...preview, items: [], actions: [...preview.actions] }
  const older = { ...active, revision: 1 }
  const unrelated = { ...active, request_id: "unrelated" }
  const status = {
    type: "status_card" as const,
    id: preview.request_id,
    status: "pending_review",
    revision: 3,
    items: [],
    actions: [],
  }
  const history = requestCardHistory([older, unrelated, active, status])
  equal(previewHistoryLabel(older, history.get(older)), "已过期")
  equal(previewHistoryLabel(active, history.get(active)), "已提交")
  equal(previewHistoryLabel(unrelated, history.get(unrelated)), undefined)
  equal(history.has(status), false)

  const refreshed = { ...status }
  const withdrawn = { ...status, status: "withdrawn", revision: 4 }
  const afterWithdrawal = requestCardHistory([
    active,
    status,
    refreshed,
    withdrawn,
  ])
  equal(afterWithdrawal.get(status), withdrawn)
  equal(previewHistoryLabel(active, afterWithdrawal.get(active)), "已撤回")
})

test("a server-invalidated preview has a label even without a later card", () => {
  const invalidated = { ...preview, items: [], actions: [] }
  equal(previewHistoryLabel(invalidated, undefined), "已过期")
})
