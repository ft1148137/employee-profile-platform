import { expect, test } from "@playwright/test"
import type { ChangeRequestPublic, ItemInput } from "../src/client"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
const password = process.env.DEMO_USER_PASSWORD!

for (const [decision, summary] of [
  ["changes_requested", "1 项批准、2 项退回"],
  ["rejected", "1 项批准、2 项拒绝"],
  ["approved", "已批准 · 3 项改动"],
] as const) {
  test(`request summary matches item decisions: ${decision}`, async ({
    page,
  }) => {
    await page.route(
      "**/employees/*/change-requests?status=all",
      async (route) => {
        const body = await (await route.fetch()).json()
        const original: ChangeRequestPublic = body.data.find(
          (r: ChangeRequestPublic) => r.origin === "initial_sync",
        )
        const item = original.items![0]
        await route.fulfill({
          json: {
            count: 1,
            data: [
              {
                ...original,
                status: "approved",
                items: ["approved", decision, decision].map((value, index) => ({
                  ...item,
                  id: crypto.randomUUID(),
                  decision: value,
                  capability: {
                    ...item.capability,
                    name: `合成能力${index + 1}`,
                  },
                })),
              },
            ],
          },
        })
      },
    )
    await logInUser(page, "employee.platform@demo.example.com", password)
    await page.getByRole("link", { name: "我的画像", exact: true }).click()
    const processed = page.getByRole("region", { name: "已处理请求" })
    await expect(processed).toContainText(summary)
    await expect(processed).toContainText("合成能力1：批准")
    await expect(processed).toContainText(
      `合成能力2：${decision === "changes_requested" ? "退回修改" : decision === "rejected" ? "拒绝" : "批准"}`,
    )
  })
}

test("old initial draft previews the current value and submits an edited proposal", async ({
  page,
}) => {
  let draft: ChangeRequestPublic
  let statement = "旧初稿建议"
  let submitted = false
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
  await page.route("**/me/requests/*/preview", (route) =>
    route.fulfill({
      json: {
        request_id: draft.id,
        revision: draft.revision,
        reviewers: [{ name: "合成经理", role: "部门经理" }],
        items: [
          {
            before: {
              id: currentId,
              statement: "经理已发布的当前判断",
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
              after: { ...draft.items![0].after, statement },
            },
            current_changed_at: "2026-11-14T08:00:00Z",
            warning: null,
          },
        ],
      },
    }),
  )
  let saves = 0
  await page.route("**/me/requests/*/items", async (route) => {
    const item: ItemInput = route.request().postDataJSON().items[0]
    if (++saves === 1) {
      await route.fulfill({
        status: 422,
        json: {
          detail: [{ index: 0, message: "等级 key 不在该能力的等级中" }],
        },
      })
      return
    }
    statement = item.statement
    await route.fulfill({ json: draft })
  })
  await page.route("**/me/requests/*/submit", async (route) => {
    expect(route.request().postDataJSON().revision).toBe(draft.revision)
    submitted = true
    await route.fulfill({ json: { ...draft, status: "pending_review" } })
  })
  await logInUser(page, "employee.platform@demo.example.com", password)
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  await page.getByRole("button", { name: "直接提交", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("经理已发布的当前判断")
  await expect(dialog).toContainText(
    "经理或管理员已在 2026/11/14 16:00:00（北京时间）修改过这项",
  )
  await dialog.getByRole("button", { name: "返回修改" }).click()
  await dialog
    .getByRole("textbox", { name: "陈述", exact: true })
    .fill("本人基于当前值补充")
  await dialog.getByRole("button", { name: "预览并提交" }).click()
  await expect(dialog.getByRole("alert")).toContainText(
    "第 1 项：等级 key 不在该能力的等级中",
  )
  await dialog.getByRole("button", { name: "预览并提交" }).click()
  await expect(dialog).toContainText("本人基于当前值补充")
  await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect(
    page.getByRole("region", { name: "其他待处理请求" }),
  ).toContainText("待经理审批")
})
