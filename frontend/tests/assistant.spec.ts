import path from "node:path"
import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

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
    const names = [
      "聊天",
      "收件箱",
      ...(manager ? ["待审批"] : []),
      "我的画像",
      ...(manager ? ["我的团队", "组织架构"] : []),
      "设置",
    ]
    await expect(rail.getByRole("link")).toHaveCount(names.length)
    for (const name of names)
      await expect(
        rail.getByRole("link", { name: new RegExp(`^${name}(?: \\d+)?$`) }),
      ).toBeInViewport()
    await expect(page.getByRole("region", { name: "历史对话" })).toBeVisible()
    await expect(
      page.getByRole("navigation", { name: "面包屑" }),
    ).toContainText("助手")
    await expect(page.getByLabel("发给机器人的消息")).toBeEnabled()
    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
    await page.screenshot({
      path: path.resolve(
        import.meta.dirname,
        `../../docs/screenshots/m4.3/assistant-${manager ? "manager" : "employee"}.png`,
      ),
    })
    await rail.getByRole("link", { name: "我的画像", exact: true }).click()
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
  const input = page.getByLabel("发给机器人的消息")
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("请修改合成能力")
  expect(turns).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole("region", { name: "历史对话" })).toHaveCount(0)
  await page.getByRole("button", { name: "会话", exact: true }).click()
  const sheet = page.getByRole("dialog", { name: "会话", exact: true })
  await sheet.getByRole("button", { name: /合成历史会话/ }).click()
  await expect(sheet).toBeHidden()
  await expect(page).toHaveURL(`/assistant?session=${id}`)
  await expect(page.getByRole("log")).toContainText("合成历史内容")
  await expect(page.getByRole("navigation", { name: "面包屑" })).toContainText(
    "合成历史会话",
  )
  await page.getByRole("button", { name: "会话", exact: true }).click()
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
  await expect(
    page
      .getByTestId("app-rail")
      .getByRole("link", { name: "聊天", exact: true }),
  ).toHaveCount(0)
  await page.goto("/assistant")
  await expect(page.getByRole("alert")).toContainText("没有员工身份")
})
