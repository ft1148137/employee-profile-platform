import { expect, test } from "@playwright/test"
import type { ChangeRequestPublic } from "../src/client"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("direct submit shows current values and a stale confirmation reloads the preview", async ({
  page,
}) => {
  let draft: ChangeRequestPublic
  let submitted = false
  let previews = 0
  let submits = 0
  await page.route(
    "**/employees/*/change-requests?status=all",
    async (route) => {
      if (!draft) {
        const body = await (await route.fetch()).json()
        draft = body.data.find(
          (r: ChangeRequestPublic) => r.origin === "initial_sync",
        )
        draft = { ...draft, items: [draft.items![0]] }
      }
      await route.fulfill({
        json: {
          count: 1,
          data: [
            {
              ...draft,
              status: submitted ? "pending_review" : "pending_owner",
            },
          ],
        },
      })
    },
  )
  const currentId = crypto.randomUUID()
  await page.route("**/me/requests/*/preview", (route) => {
    previews++
    return route.fulfill({
      json: {
        request_id: draft.id,
        revision: draft.revision,
        base_revision: previews === 1 ? 8 : 9,
        reviewers: [],
        items: [
          {
            before: {
              id: currentId,
              statement:
                previews === 1
                  ? "经理已发布的当前判断"
                  : "经理再次修改后的判断",
              level: "routine",
              assessment_status: "confirmed",
              confidence: null,
              explanation: null,
              generated_by: { actor_type: "user" },
              evidence: [],
            },
            after: {
              ...draft.items![0],
              op: "modify",
              target_claim_id: currentId,
            },
            current_changed_at: "2026-11-14T08:00:00Z",
            warning: null,
          },
        ],
      },
    })
  })
  await page.route("**/me/requests/*/submit", async (route) => {
    const body = route.request().postDataJSON()
    expect(body.revision).toBe(draft.revision)
    if (++submits === 1) {
      expect(body.base_revision).toBe(8)
      await route.fulfill({
        status: 409,
        json: {
          detail: {
            code: "stale_initial_draft",
            message: "经理再次改过自动化测试，请先核对最新预览",
            capabilities: [{ key: "automated_testing", name: "自动化测试" }],
          },
        },
      })
      return
    }
    expect(body.base_revision).toBe(9)
    submitted = true
    await route.fulfill({ json: { ...draft, status: "pending_review" } })
  })
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.locator("summary", { hasText: /待你确认：初始画像草稿/ }).click()
  await page.getByRole("button", { name: "直接提交", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("修改前")
  await expect(dialog).toContainText("经理已发布的当前判断")
  expect(submits).toBe(0)
  await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(dialog).toContainText("经理再次修改后的判断")
  await expect(dialog).toContainText("请先核对最新预览")
  expect(submitted).toBe(false)
  expect(submits).toBe(1)
  await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(submitted).toBe(true)
})
