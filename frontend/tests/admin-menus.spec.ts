import { expect, type Page, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
test.beforeEach(async ({ page }) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.waitForURL(
    (url) => url.pathname !== "/" && !url.pathname.startsWith("/login"),
  )
})

const groups = [
  {
    space: "组织",
    path: "/admin/rules",
    tabs: [
      ["dictionary", "能力词典"],
      ["approval", "审批规则"],
      ["reminders", "定期提醒"],
      ["capacity", "需求与容量规则"],
    ],
  },
  {
    space: "设置",
    path: "/admin/data-ai",
    tabs: [
      ["sources", "数据来源"],
      ["model", "模型"],
      ["bot", "内置机器人"],
      ["im", "IM 入口"],
      ["mcp", "外部 AI 助手"],
    ],
  },
]

const navigation = (page: Page) =>
  page.getByRole("navigation", { name: "空间导航" })

test("management sections and audit open from the space navigation", async ({
  page,
}) => {
  test.setTimeout(90000)
  for (const name of ["连接", "首页", "首次设置"])
    await expect(page.getByRole("link", { name, exact: true })).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "更多", exact: true }),
  ).toHaveCount(0)
  for (const group of groups) {
    await page
      .getByTestId("app-rail")
      .getByRole("link", { name: group.space, exact: true })
      .click()
    for (const [tab, name] of group.tabs) {
      const link = navigation(page).getByRole("link", { name, exact: true })
      await link.click()
      await expect(page).toHaveURL(`${group.path}?tab=${tab}`)
      await expect(link).toHaveAttribute("aria-current", "page")
      await expect(
        navigation(page).locator('[aria-current="page"]'),
      ).toHaveCount(1)
      const main = page.getByRole("main")
      await expect(main.getByRole("tablist")).toHaveCount(0)
      if (tab === "approval")
        await expect(main.getByRole("form", { name: "审批规则" })).toBeVisible()
      else if (tab === "reminders")
        await expect(
          main.getByRole("region", { name: "持续更新提醒" }),
        ).toBeVisible()
      else
        await expect(
          main.getByRole("heading", { name, exact: true }),
        ).toBeVisible()
    }
  }
  await navigation(page).getByRole("link", { name: "审计日志" }).click()
  await expect(
    page.getByRole("heading", { name: "审计日志", exact: true }),
  ).toBeVisible()
})

test("direct section links survive reload and history, invalid values use the first", async ({
  page,
}) => {
  test.setTimeout(90000)
  const active = () => navigation(page).locator('[aria-current="page"]')
  for (const group of groups) {
    for (const [tab, name] of group.tabs) {
      await page.goto(`${group.path}?tab=${tab}`)
      await expect(active()).toHaveText(name)
    }
    await page.reload()
    await expect(active()).toHaveText(group.tabs[group.tabs.length - 1][1])
    await page.goto(`${group.path}?tab=unknown`)
    await expect(active()).toHaveText(group.tabs[0][1])
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/admin/rules?tab=reminders")
  const open = page.getByRole("button", { name: "打开空间导航" })
  const drawer = page.getByRole("dialog", { name: "空间导航", exact: true })
  await expect(navigation(page)).toHaveCount(0)
  await open.click()
  await expect(drawer).toBeVisible()
  await drawer.getByRole("link", { name: "审批规则", exact: true }).click()
  await expect(drawer).toBeHidden()
  await expect(page.getByRole("form", { name: "审批规则" })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole("region", { name: "持续更新提醒" })).toBeVisible()
  await open.click()
  await expect(active()).toHaveText("定期提醒")
  await page.keyboard.press("Escape")
  await expect(open).toBeFocused()
  await expect(page.getByRole("heading", { name: "自动起草设置" })).toHaveCount(
    0,
  )
  await page.screenshot({
    path: "test-results/admin-menus-mobile.png",
    fullPage: true,
  })
})

test("legacy addresses redirect to their new locations", async ({ page }) => {
  test.setTimeout(90000)
  const aliases = [
    ["accounts", "/employees"],
    ["roster", "/admin/assistant"],
    ["roles", "/employees?filter=roles"],
    ["identities", "/employees?filter=identities"],
    ["appeals", "/employees?filter=appeals"],
    ["drafting", "/admin/data-ai?tab=model"],
    ["profile-imports", "/admin/assistant"],
    ["sources", "/admin/data-ai?tab=sources"],
    ["model", "/admin/data-ai?tab=model"],
    ["bot", "/admin/data-ai?tab=bot"],
    ["connections", "/admin/data-ai?tab=im"],
    ["connections?tab=im", "/admin/data-ai?tab=im"],
    ["connections?tab=mcp", "/admin/data-ai?tab=mcp"],
    ["connections?tab=unknown", "/admin/data-ai?tab=im"],
    ["im", "/admin/data-ai?tab=im"],
    ["assistants", "/admin/data-ai?tab=mcp"],
    ["progress", "/admin/assistant"],
    ["", "/admin/assistant"],
  ]
  for (const [tab, target] of [
    ["", "/employees"],
    ["accounts", "/employees"],
    ["roster", "/admin/assistant"],
    ["org-chart", "/org-chart"],
    ["roles", "/employees?filter=roles"],
    ["identities", "/employees?filter=identities"],
    ["appeals", "/employees?filter=appeals"],
  ]) {
    await page.goto(`/admin/people${tab ? `?tab=${tab}` : ""}`)
    await expect(page).toHaveURL(target)
  }
  await page.goto("/admin/rules?tab=profile-import")
  await expect(page).toHaveURL("/admin/assistant")
  for (const [old, target] of aliases) {
    await page.goto(`/admin/${old}`)
    await expect(page).toHaveURL(target)
  }
})

test("personal settings has three links and advanced options start collapsed", async ({
  page,
}) => {
  await page.goto("/settings")
  for (const name of ["账号与安全", "连接与集成", "偏好与代理"])
    await expect(
      navigation(page).getByRole("link", { name, exact: true }),
    ).toBeVisible()
  await expect(page.getByRole("main").getByRole("tablist")).toHaveCount(0)
  await expect(
    page.getByRole("link", { name: "查看企业能力词典" }),
  ).toHaveCount(0)
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await navigation(page).getByRole("link", { name: "能力词典" }).click()
  await expect(page).toHaveURL("/admin/rules?tab=dictionary")
  await page.goto("/admin/data-ai?tab=bot")
  await expect(page.getByLabel("聊天原文保留天数")).toBeHidden()
  await page.getByText("高级设置：聊天原文保留", { exact: true }).click()
  await expect(page.getByLabel("聊天原文保留天数")).toBeVisible()
})
