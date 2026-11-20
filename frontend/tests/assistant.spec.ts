import path from "node:path"
import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("organization navigation wins while the post-login assistant is still loading", async ({
  page,
}) => {
  let markRequested = () => {}
  const requested = new Promise<void>((resolve) => {
    markRequested = resolve
  })
  let unblock = () => {}
  const pending = new Promise<void>((resolve) => {
    unblock = resolve
  })
  await page.route(
    /\/(?:assets\/assistant-[^/]+\.js|src\/routes\/_layout\/assistant\.tsx)(?:\?|$)/,
    async (route) => {
      markRequested()
      await pending
      await route.continue()
    },
  )
  try {
    await logInUser(
      page,
      "employee.platform@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(page).toHaveURL("/assistant")
    await requested
    await page.getByRole("link", { name: "组织", exact: true }).click()
    await expect(page).toHaveURL(/\/employees\/[^/]+$/)
    await expect(
      page.getByRole("heading", { name: "张伟", exact: true }),
    ).toBeVisible()
  } finally {
    unblock()
  }
})

test("admin unsent text returns with its conversation after leaving the page", async ({
  page,
}) => {
  const session = crypto.randomUUID()
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  await page.route(
    `**/admin-assistant/sessions/${session}/messages?*`,
    (route) => route.fulfill({ json: [] }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto(`/admin/assistant?session=${session}`)
  const input = page.getByLabel("发给管理助手的消息")
  await expect(input).toBeEnabled()
  await input.fill("尚未发送的合成管理说明")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await expect(page).toHaveURL("/employees")
  await page.goBack()
  await expect(input).toHaveValue("尚未发送的合成管理说明")
})

test("mobile unsent text survives page navigation, stays in its session and clears after sending", async ({
  page,
}) => {
  const email = `draft-${crypto.randomUUID()}@example.com`
  const password = "synthetic-draft-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  const session = crypto.randomUUID()
  await page.route("**/bot/sessions?*", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({
          json: {
            data: [
              {
                id: session,
                preview: "合成草稿会话",
                started_at: new Date().toISOString(),
                last_activity_at: new Date().toISOString(),
                turn_count: 1,
              },
            ],
            retention_days: 0,
          },
        })
      : route.continue(),
  )
  await page.route(`**/bot/sessions/${session}/messages?*`, (route) =>
    route.fulfill({ json: [] }),
  )
  await page.setViewportSize({ width: 390, height: 844 })
  await logInUser(page, email, password)
  const input = page.getByLabel("发给助手的消息")
  await expect(input).toBeEnabled()
  const currentSession = () =>
    page.evaluate(
      () =>
        Object.entries(localStorage).find(([key]) =>
          key.startsWith("bot-session:"),
        )?.[1],
    )
  const chooseSession = async () => {
    const previousInput = await input.elementHandle()
    await page
      .getByRole("button", { name: "打开空间导航", exact: true })
      .click()
    await page
      .getByRole("dialog", { name: "空间导航", exact: true })
      .getByRole("button", { name: /合成草稿会话/ })
      .click()
    await expect(page).toHaveURL(`/assistant?session=${session}`)
    await expect.poll(currentSession).toBe(session)
    await expect
      .poll(() => previousInput!.evaluate((element) => element.isConnected))
      .toBe(false)
    await expect(input).toBeEnabled()
  }
  await chooseSession()
  await expect(input).toBeEnabled()
  await input.fill("尚未发送的合成经历")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.getByRole("link", { name: /^协同/ }).click()
  await expect(input).toHaveValue("尚未发送的合成经历")
  await page.getByRole("button", { name: "打开空间导航", exact: true }).click()
  await page
    .getByRole("dialog", { name: "空间导航", exact: true })
    .getByRole("button", { name: "新对话", exact: true })
    .click()
  await expect(page).toHaveURL("/assistant")
  await expect
    .poll(async () => {
      const id = await currentSession()
      return !!id && id !== session
    })
    .toBe(true)
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("")
  await chooseSession()
  await expect(input).toHaveValue("尚未发送的合成经历")
  await page.getByRole("button", { name: "打开空间导航", exact: true }).click()
  await page
    .getByRole("dialog", { name: "空间导航", exact: true })
    .getByRole("button", { name: "新对话", exact: true })
    .click()
  await expect
    .poll(async () => {
      const id = await currentSession()
      return !!id && id !== session
    })
    .toBe(true)
  await expect(input).toBeEnabled()
  await page.goBack()
  await expect(page).toHaveURL(`/assistant?session=${session}`)
  await expect(input).toHaveValue("尚未发送的合成经历")
  await page.route("**/turns?*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: { text: "合成回复", cards: [], stopped: false } })}\n`,
    }),
  )
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(input).toHaveValue("")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.getByRole("link", { name: /^协同/ }).click()
  await expect(input).toHaveValue("")
  await input.fill("刷新后不保留")
  await page.reload()
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("")
})

for (const manager of [false, true]) {
  test(`${manager ? "manager" : "employee"} lands in the shared assistant with role menus`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(
      page,
      `${manager ? "manager" : "employee"}.platform@demo.example.com`,
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(page).toHaveURL("/assistant")
    const rail = page.getByTestId("app-rail")
    await expect(rail.getByRole("link")).toHaveText(
      ["协同", "组织", "设置"].map(
        (name) => new RegExp(`^${name}(?:\\s*\\d+)?$`),
      ),
    )
    const spacePanel = page.getByRole("navigation", { name: "空间导航" })
    await expect(
      spacePanel.getByRole("link", { name: "AI 助手" }),
    ).toBeInViewport()
    await expect(
      spacePanel.getByRole("link", { name: /^收件箱/ }),
    ).toBeInViewport()
    if (manager) {
      await expect(
        spacePanel.getByRole("link", { name: /^待审批/ }),
      ).toBeInViewport()
    } else {
      await expect(
        spacePanel.getByRole("link", { name: /^待审批/ }),
      ).toHaveCount(0)
    }
    await expect(page.getByRole("region", { name: "历史对话" })).toBeVisible()
    await expect(
      page.getByRole("navigation", { name: "面包屑" }),
    ).toContainText("助手")
    await expect(page.getByLabel("发给助手的消息")).toBeEnabled()
    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
    await page.screenshot({
      path: path.resolve(
        import.meta.dirname,
        `../../docs/screenshots/m4.3/assistant-${manager ? "manager" : "employee"}.png`,
      ),
    })
    await rail.getByRole("link", { name: "组织", exact: true }).click()
    await expect(page).toHaveURL(/\/employees\/[^/]+$/)
    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
  })
}

test("prompt only prefills and mobile history selects sessions and opens a new conversation", async ({
  page,
}) => {
  const email = `assistant-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  const id = crypto.randomUUID()
  let turns = 0
  await page.route("**/bot/sessions?*", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({
          json: {
            data: [
              {
                id,
                preview: "合成历史会话",
                started_at: new Date().toISOString(),
                last_activity_at: new Date().toISOString(),
                turn_count: 1,
              },
            ],
            retention_days: 0,
          },
        })
      : route.continue(),
  )
  await page.route(`**/bot/sessions/${id}/messages?*`, (route) =>
    route.fulfill({
      json: [{ role: "user", payload: { text: "合成历史内容" } }],
    }),
  )
  page.on("request", (request) => {
    if (request.url().includes("/turns?")) turns++
  })
  await logInUser(page, email, password)
  await page.goto(`/assistant?prompt=${encodeURIComponent("请修改合成能力")}`)
  const input = page.getByLabel("发给助手的消息")
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("请修改合成能力")
  expect(turns).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole("region", { name: "历史对话" })).toHaveCount(0)
  await page.getByRole("button", { name: "打开空间导航", exact: true }).click()
  const sheet = page.getByRole("dialog", { name: "空间导航", exact: true })
  await sheet.getByRole("button", { name: /合成历史会话/ }).click()
  await expect(sheet).toBeHidden()
  await expect(page).toHaveURL(`/assistant?session=${id}`)
  await expect(page.getByRole("log")).toContainText("合成历史内容")
  await expect(page.getByRole("navigation", { name: "面包屑" })).toContainText(
    "合成历史会话",
  )
  await page.getByRole("button", { name: "打开空间导航", exact: true }).click()
  await sheet.getByRole("button", { name: "新对话", exact: true }).click()
  await expect(sheet).toBeHidden()
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("")
  await expect(page.getByRole("log")).not.toContainText("合成历史内容")
  expect(turns).toBe(0)
})

test("manager without employee identity keeps the team landing and cannot start personal chat", async ({
  page,
}) => {
  await page.route("**/api/v1/me/memberships", async (route) => {
    const response = await route.fetch()
    await route.fulfill({
      response,
      json: (await response.json()).map((membership: object) => ({
        ...membership,
        employee_id: null,
      })),
    })
  })
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL("/employees")
  await page.getByRole("link", { name: /^协同/ }).click()
  await expect(
    page
      .getByRole("navigation", { name: "空间导航" })
      .getByRole("link", { name: "AI 助手", exact: true }),
  ).toHaveCount(0)
  await page.goto("/assistant")
  await expect(page.getByRole("alert")).toContainText("没有员工身份")
})
