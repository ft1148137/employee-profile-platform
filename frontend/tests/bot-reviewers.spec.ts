import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("assistant preview and status name the reviewers", async ({ page }) => {
  const email = `reviewers-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  const previewId = crypto.randomUUID()
  await page.route("**/bot/sessions/*/turns?org_id=*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({
        type: "final",
        response: {
          messages: [
            {
              type: "preview_card",
              request_id: previewId,
              revision: 1,
              items: [],
              actions: [
                {
                  action: "confirm_submit",
                  request_id: previewId,
                  revision: 1,
                },
              ],
              reviewers: [{ name: "合成经理", role: "直属上级" }],
            },
            {
              type: "status_card",
              id: crypto.randomUUID(),
              revision: 2,
              status: "pending_review",
              items: [],
              actions: [],
              reviewers: [
                { name: "合成经理", role: "直属上级" },
                { name: "合成代理人", role: "代理审批" },
              ],
            },
          ],
          steps: [],
        },
      })}\n`,
    }),
  )
  await logInUser(page, email, password)
  const panel = page.getByRole("region", { name: "助手", exact: true })
  await panel.getByLabel("发给助手的消息").fill("查看请求进度")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await expect(panel.getByLabel("变更预览", { exact: true })).toContainText(
    "提交后由 合成经理（直属上级）审批",
  )
  await expect(panel.getByLabel("请求进度", { exact: true })).toContainText(
    "待经理审批：合成经理（直属上级）、合成代理人（代理审批）",
  )
})

test("mixed request shows item results and refreshes a stale withdrawal", async ({
  page,
}) => {
  const email = `mixed-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  const id = crypto.randomUUID()
  const items = [
    {
      capability: { key: "first", name: "合成免审能力" },
      decision: "approved",
    },
    { capability: { key: "second", name: "合成送审能力" }, decision: null },
  ]
  const pending = {
    type: "status_card",
    id,
    revision: 2,
    status: "pending_review",
    items,
    actions: [{ action: "withdraw", request_id: id, revision: 2 }],
  }
  let withdrawn = false
  await page.route("**/bot/sessions/*/messages?org_id=*", (route) =>
    route.fulfill({
      json: withdrawn
        ? [
            {
              id: crypto.randomUUID(),
              role: "assistant",
              payload: {
                messages: [{ ...pending, status: "withdrawn", actions: [] }],
              },
              created_at: new Date().toISOString(),
            },
          ]
        : [],
    }),
  )
  await page.route("**/bot/sessions/*/turns?org_id=*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: { messages: [pending] } })}\n`,
    }),
  )
  await page.route("**/bot/sessions/*/actions?org_id=*", (route) => {
    withdrawn = true
    return route.fulfill({ status: 409, json: { detail: "当前状态不能撤回" } })
  })
  await logInUser(page, email, password)
  const panel = page.getByRole("region", { name: "助手", exact: true })
  await panel.getByLabel("发给助手的消息").fill("查看请求进度")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  const card = panel.getByRole("region", { name: "请求进度" })
  await expect(card.getByText(/合成免审能力/)).toContainText("已发布（暂定）")
  await expect(card.getByText(/合成送审能力/)).toContainText("待经理审批")
  await expect(card).toContainText("撤回仅取消待经理审批项，已发布项不受影响")
  await card.getByRole("button", { name: "撤回", exact: true }).click()
  await expect(panel.getByRole("alert")).toContainText("这张卡片无法撤回")
  await expect(card.getByRole("heading")).toHaveText("已撤回")
  await expect(
    card.getByRole("button", { name: "撤回", exact: true }),
  ).toHaveCount(0)
  await page.reload()
  await expect(card.getByText(/合成免审能力/)).toContainText("已发布（暂定）")
  await expect(card.getByText(/合成送审能力/)).toContainText("已撤回")
})
