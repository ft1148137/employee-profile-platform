import { expect, test } from "@playwright/test"
import { mockCompletedSetup } from "./utils/adminAssistant"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const assistant of ["bot", "admin-assistant"] as const) {
  test(`${assistant} shows safe stream errors and allows another message`, async ({
    page,
  }) => {
    await mockCompletedSetup(page)
    if (assistant === "admin-assistant")
      await page.route("**/admin-assistant/availability?*", (route) =>
        route.fulfill({ json: { available: true, reason: "" } }),
      )
    await page.route(`**/${assistant}/sessions/*/messages?*`, (route) =>
      route.fulfill({ json: [] }),
    )
    let events: object[] = []
    let disconnected = false
    await page.route(`**/${assistant}/sessions/*/turns?*`, (route) =>
      disconnected
        ? route.abort("failed")
        : route.fulfill({
            contentType: "application/x-ndjson",
            body: `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
          }),
    )
    if (assistant === "bot") {
      const email = `m5-errors-${crypto.randomUUID()}@example.com`
      const password = "synthetic-password-2026"
      await createUser({ email, password, withEmptyProfile: true })
      await logInUser(page, email, password)
      await page.goto("/assistant")
    } else {
      await logInUser(
        page,
        "admin@demo.example.com",
        process.env.DEMO_USER_PASSWORD!,
      )
      await page.goto(`/admin/assistant?session=${crypto.randomUUID()}`)
    }
    const panel = page.getByRole("region", {
      name: assistant === "bot" ? "助手" : "管理助手",
      exact: true,
    })
    const input = panel.getByLabel(
      assistant === "bot" ? "发给助手的消息" : "发给管理助手的消息",
    )
    for (const message of [
      "模型服务暂时不可用，请稍后重试",
      "模型额度暂时用完，请稍后重试",
    ]) {
      events = [
        { type: "text", text: "未完成的回复" },
        { type: "error", message },
      ]
      await input.fill("合成错误问题")
      await input.press("Enter")
      await expect(panel.getByRole("alert")).toHaveText(message)
      await expect(
        panel.getByText("未完成的回复", { exact: true }),
      ).toHaveCount(0)
      await expect(input).toBeEnabled()
    }
    events = [{ type: "text", text: "断流的片段" }]
    await input.fill("合成断流问题")
    await input.press("Enter")
    await expect(panel.getByRole("alert")).toHaveText("对话连接中断，请重试")
    disconnected = true
    const failedRequest = page.waitForEvent(
      "requestfailed",
      (request) =>
        request.url().includes(`/${assistant}/sessions/`) &&
        request.url().includes("/turns?"),
    )
    await input.fill("合成网络错误")
    await input.press("Enter")
    await failedRequest
    await expect(panel.getByRole("alert")).toHaveText("对话连接中断，请重试")
    await expect(input).toBeEnabled()
    disconnected = false
    events = [
      {
        type: "final",
        response: { messages: [{ type: "text", text: "恢复成功" }] },
      },
    ]
    await input.fill("重试")
    await input.press("Enter")
    await expect(panel.getByText("恢复成功", { exact: true })).toBeVisible()
    await expect(input).toBeEnabled()
  })
}

test("dictionary distinguishes withdrawn evidence from a manual suggestion", async ({
  page,
}) => {
  await mockCompletedSetup(page)
  await page.route("**/orgs/*/capabilities/proposals", (route) =>
    route.fulfill({
      json: {
        data: [true, false].map((withdrawn) => ({
          id: crypto.randomUUID(),
          name: withdrawn ? "已撤回依据的建议" : "手工建议",
          description: "合成说明",
          reason: "合成理由",
          category: null,
          evidence_count: 0,
          evidence_titles: [],
          evidence_withdrawn: withdrawn,
          employee_count: 0,
          proposed_by_employee_name: withdrawn ? null : "合成员工",
          updated_at: new Date().toISOString(),
        })),
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules?tab=dictionary#dictionary-editor")
  const withdrawn = page.getByRole("article", {
    name: "建议：已撤回依据的建议",
    exact: true,
  })
  const manual = page.getByRole("article", {
    name: "建议：手工建议",
    exact: true,
  })
  await expect(withdrawn.getByText("依据已撤回", { exact: true })).toBeVisible()
  await expect(manual).toBeVisible()
  await expect(manual.getByText("依据已撤回", { exact: true })).toHaveCount(0)
})
