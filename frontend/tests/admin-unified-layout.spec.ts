import path from "node:path"
import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const size of [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
]) {
  test(`unified sidebar keeps menus visible with 25 conversations at ${size.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(size)
    const entries = Array.from({ length: 25 }, (_, i) => ({
      id: crypto.randomUUID(),
      preview: `合成管理对话 ${i + 1}`,
      started_at: new Date().toISOString(),
      last_activity_at: new Date(Date.now() - i * 60000).toISOString(),
      turn_count: 1,
    }))
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({ json: { available: true, reason: "" } }),
    )
    await page.route("**/admin-assistant/sessions?*", (route) =>
      route.fulfill({ json: { data: entries, retention_days: 7 } }),
    )
    await page.route("**/admin-assistant/sessions/*/messages?*", (route) => {
      const entry = entries.find((entry) =>
        route.request().url().includes(entry.id),
      )!
      return route.fulfill({
        json: [
          {
            id: crypto.randomUUID(),
            role: "user",
            payload: { text: entry.preview },
            created_at: entry.started_at,
          },
        ],
      })
    })
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.goto("/admin/assistant")
    const sidebar = page.getByRole("region", { name: "管理助手会话" })
    await expect(sidebar.getByRole("link")).toHaveCount(25)
    await expect(
      page.getByRole("region", { name: "今天要处理的事" }),
    ).toBeVisible()
    for (const entry of entries) {
      const row = sidebar.getByRole("link", {
        name: entry.preview,
        exact: true,
      })
      await row.scrollIntoViewIfNeeded()
      await expect(row).toBeInViewport({ ratio: 1 })
      await expect(row).toContainText("管理助手 · 1 轮")
      await expect(row.locator("time")).toHaveText(
        /^(?:\d{2}:\d{2}|\d{1,2}\/\d{1,2})$/,
      )
    }
    const rail = page.getByTestId("app-rail")
    for (const link of await rail.getByRole("link").all()) {
      await expect(link).toBeInViewport({ ratio: 1 })
    }
    for (const name of ["首页", "能力与规则", "数据与 AI"]) {
      await expect(
        page
          .getByRole("navigation", { name: "管理菜单" })
          .getByRole("link", { name, exact: true }),
      ).toBeInViewport({ ratio: 1 })
    }
    await expect(page.getByText("更多", { exact: true })).toBeInViewport({
      ratio: 1,
    })
    await expect(page.getByTestId("user-menu")).toBeInViewport({ ratio: 1 })
    await sidebar
      .getByRole("link", { name: entries[1].preview, exact: true })
      .click()
    await expect(page).toHaveURL(`/admin/assistant?session=${entries[1].id}`)
    await expect(page.getByRole("log")).toContainText(entries[1].preview)
    await expect(
      sidebar.getByRole("link", { name: entries[1].preview, exact: true }),
    ).toHaveAttribute("aria-current", "page")
    // Retaining the same DOM node proves that route switches do not remount the sidebar.
    await expect(
      page.getByRole("navigation", { name: "面包屑" }),
    ).toContainText(entries[1].preview)
    const node = await rail.elementHandle()
    await page.getByRole("link", { name: "员工", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "员工", exact: true }),
    ).toBeVisible()
    expect(await node?.evaluate((element) => element.isConnected)).toBe(true)
    await expect(sidebar).toHaveCount(0)
    await expect(
      page.getByRole("columnheader", { name: "工号", exact: true }),
    ).toBeVisible()
    if (size.width === 1280)
      await page.screenshot({
        path: path.resolve(
          import.meta.dirname,
          "../../docs/screenshots/m4.2/admin-rail-people.png",
        ),
      })
    await page.getByRole("link", { name: "聊天", exact: true }).click()
    await sidebar
      .getByRole("link", { name: entries[0].preview, exact: true })
      .click()
    await expect(page.getByRole("log")).toContainText(entries[0].preview)
    await page.screenshot({
      path: path.resolve(
        import.meta.dirname,
        `../../docs/screenshots/m4.2/admin-unified-${size.width}x${size.height}.png`,
      ),
    })
    await page.getByRole("button", { name: "新对话", exact: true }).click()
    await expect(
      page.getByRole("navigation", { name: "面包屑" }),
    ).toContainText("新对话")
    await expect(page).toHaveURL("/admin/assistant")
    await expect(page.getByRole("log")).not.toContainText(entries[0].preview)
    await sidebar
      .getByRole("link", { name: entries[0].preview, exact: true })
      .click()
    await page.goto("/admin/assistant")
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            Object.entries(localStorage).find(([key]) =>
              key.startsWith("admin-assistant:"),
            )?.[1],
        ),
      )
      .toBe("")
    await expect(
      page.getByRole("region", { name: "今天要处理的事" }),
    ).toBeVisible()
  })
}

test("unconfigured model allows history and every manual menu", async ({
  page,
}) => {
  const reason = "请先配置模型，可以继续使用手工页面。"
  const id = crypto.randomUUID()
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: false, reason } }),
  )
  await page.route("**/admin-assistant/sessions?*", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id,
            preview: "合成历史对话",
            started_at: new Date().toISOString(),
            last_activity_at: new Date().toISOString(),
            turn_count: 1,
          },
        ],
        retention_days: 7,
      },
    }),
  )
  await page.route("**/admin-assistant/sessions/*/messages?*", (route) =>
    route.fulfill({
      json: [
        {
          id: crypto.randomUUID(),
          role: "user",
          payload: { text: "历史内容仍可查看" },
          created_at: new Date().toISOString(),
        },
      ],
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/assistant")
  await expect(page.getByRole("log")).toContainText(reason)
  await expect(page.getByLabel("发给管理助手的消息")).toBeDisabled()
  await page.getByRole("link", { name: "合成历史对话", exact: true }).click()
  await expect(page.getByRole("log")).toContainText("历史内容仍可查看")
  await expect(page.getByRole("log")).toContainText(reason)
  for (const [name, route] of [
    ["首页", "/admin"],
    ["能力与规则", "/admin/rules"],
    ["数据与 AI", "/admin/data-ai"],
  ]) {
    await page
      .getByRole("navigation", { name: "管理菜单" })
      .getByRole("link", { name, exact: true })
      .click()
    await expect(page).toHaveURL(route)
  }
  for (const name of ["审计日志", "首次设置"]) {
    await page.getByRole("button", { name: "更多", exact: true }).click()
    await page.getByRole("menuitem", { name, exact: false }).click()
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible()
  }
})

test("session switch cancels the turn and preserves both conversation histories", async ({
  page,
}) => {
  const ids = [crypto.randomUUID(), crypto.randomUUID()]
  let finishTurn: (() => void) | undefined
  let finishReload: (() => void) | undefined
  let reads = 0
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  await page.route("**/admin-assistant/sessions?*", (route) =>
    route.fulfill({
      json: {
        data: ids.map((id, i) => ({
          id,
          preview: `会话${i}`,
          started_at: new Date().toISOString(),
          last_activity_at: new Date().toISOString(),
          turn_count: 1,
        })),
        retention_days: 7,
      },
    }),
  )
  await page.route(
    "**/admin-assistant/sessions/*/messages?*",
    async (route) => {
      const first = route.request().url().includes(ids[0])
      if (first && reads++ > 0)
        await new Promise<void>((resolve) => {
          finishReload = resolve
        })
      await route.fulfill({
        json: [
          {
            role: "user",
            payload: { text: first ? "第一段历史" : "第二段历史" },
          },
        ],
      })
    },
  )
  await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
    await new Promise<void>((resolve) => {
      finishTurn = resolve
    })
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: { messages: [{ type: "text", text: "第一段的新回复" }] } })}\n`,
    })
  })
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto(`/admin/assistant?session=${ids[0]}`)
  await page.getByLabel("发给管理助手的消息").fill("第一段的新问题")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect.poll(() => !!finishTurn).toBe(true)
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().includes("/turns?"),
  )
  await page.getByRole("link", { name: "会话1", exact: true }).click()
  await expect(page.getByRole("log")).toContainText("第二段历史")
  await aborted
  finishTurn!()
  await expect(page).toHaveURL(`/admin/assistant?session=${ids[1]}`)
  await page.getByRole("link", { name: "会话0", exact: true }).click()
  await expect.poll(() => !!finishReload).toBe(true)
  try {
    await expect(page.getByRole("log")).toContainText("第一段历史")
    await expect(page.getByRole("log")).not.toContainText("第一段的新回复")
    await expect(page.getByRole("log")).not.toContainText("第二段历史")
  } finally {
    finishReload!()
  }
})

test("mobile conversation sheet switches sessions and starts a new task", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const id = crypto.randomUUID()
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  await page.route("**/admin-assistant/sessions?*", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id,
            preview: "手机历史对话",
            started_at: new Date().toISOString(),
            last_activity_at: "2026-01-02T08:00:00Z",
            turn_count: 3,
          },
        ],
        retention_days: 0,
      },
    }),
  )
  await page.route("**/admin-assistant/sessions/*/messages?*", (route) =>
    route.fulfill({
      json: [{ role: "user", payload: { text: "手机对话内容" } }],
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/assistant")
  await expect(page.getByRole("region", { name: "管理助手会话" })).toHaveCount(
    0,
  )
  await page.getByRole("button", { name: "会话", exact: true }).click()
  const sheet = page.getByRole("dialog", { name: "会话", exact: true })
  await expect(sheet).toContainText("2 小时没有活动后清除")
  const row = sheet.getByRole("link", { name: "手机历史对话", exact: true })
  await expect(row).toContainText("1/2")
  await expect(row).toContainText("管理助手 · 3 轮")
  await row.click()
  await expect(sheet).toBeHidden()
  await expect(page).toHaveURL(`/admin/assistant?session=${id}`)
  await expect(page.getByRole("navigation", { name: "面包屑" })).toContainText(
    "手机历史对话",
  )
  await expect(page.getByRole("log")).toContainText("手机对话内容")
  await page.getByRole("button", { name: "会话", exact: true }).click()
  await expect(sheet.getByRole("link")).toHaveAttribute("aria-current", "page")
  await sheet.getByRole("button", { name: "新对话", exact: true }).click()
  await expect(sheet).toBeHidden()
  await expect(page).toHaveURL("/admin/assistant")
  await expect(page.getByRole("navigation", { name: "面包屑" })).toContainText(
    "新对话",
  )
  // Short windows move trailing manual entries into More, keeping the rail visible.
  await page.setViewportSize({ width: 390, height: 480 })
  await page.getByRole("button", { name: "更多", exact: true }).click()
  await expect(
    page.getByRole("menuitem", { name: "数据与 AI", exact: true }),
  ).toBeVisible()
  await page.getByRole("menuitem", { name: "数据与 AI", exact: true }).click()
  await expect(page).toHaveURL("/admin/data-ai")
})

test("employees keep their own navigation and floating bot", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const rail = page.getByTestId("app-rail")
  await expect(
    rail.getByRole("link", { name: "我的画像", exact: true }),
  ).toBeVisible()
  await expect(
    rail.getByRole("link", { name: /^收件箱(?: \d+)?$/, exact: true }),
  ).toBeVisible()
  for (const name of [
    "聊天",
    "员工",
    "首页",
    "人员",
    "能力与规则",
    "数据与 AI",
    "连接",
  ])
    await expect(rail.getByRole("link", { name, exact: true })).toHaveCount(0)
  await expect(
    rail.getByRole("button", { name: "更多", exact: true }),
  ).toHaveCount(0)
  await page
    .getByRole("button", { name: "打开画像机器人", exact: true })
    .click()
  await expect(
    page.getByRole("dialog", { name: "我的画像机器人", exact: true }),
  ).toBeVisible()
})
