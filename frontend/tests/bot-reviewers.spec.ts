import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("assistant preview and status name the reviewers", async ({ page }) => {
  const email = `reviewers-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  await page.route("**/bot/sessions/*/turns?org_id=*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({
        type: "final",
        response: {
          messages: [
            {
              type: "preview_card",
              request_id: crypto.randomUUID(),
              revision: 1,
              items: [],
              actions: [],
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
  await panel.getByLabel("发给机器人的消息").fill("查看请求进度")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await expect(panel.getByLabel("提交前预览", { exact: true })).toContainText(
    "提交后由 合成经理（直属上级）审批",
  )
  await expect(panel.getByLabel("请求进度", { exact: true })).toContainText(
    "待经理审批：合成经理（直属上级）、合成代理人（代理审批）",
  )
})
