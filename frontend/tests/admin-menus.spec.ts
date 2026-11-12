import { expect, test } from "@playwright/test"
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
    name: "人员",
    path: "/admin/people",
    tabs: [
      ["accounts", "员工与账号"],
      ["roles", "角色"],
      ["identities", "Jira 账号匹配"],
      ["appeals", "争议"],
    ],
  },
  {
    name: "能力与规则",
    path: "/admin/rules",
    tabs: [
      ["dictionary", "能力词典"],
      ["approval", "审批规则"],
      ["reminders", "定期提醒"],
    ],
  },
  {
    name: "数据与 AI",
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

test("four menus open every tab and more keeps audit and setup", async ({
  page,
}) => {
  test.setTimeout(90000)
  await expect(
    page.getByRole("navigation", { name: "管理菜单" }).getByRole("link"),
  ).toHaveCount(4)
  await expect(
    page.getByRole("link", { name: "连接", exact: true }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("menuitem", { name: "审计日志", exact: true }),
  ).toBeHidden()
  await page.getByRole("link", { name: "首页", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "同步与起草进度", exact: true }),
  ).toBeVisible()
  for (const group of groups) {
    await page.getByRole("link", { name: group.name, exact: true }).click()
    await expect(
      page
        .getByRole("tablist", { name: group.name, exact: true })
        .getByRole("tab", { selected: true }),
    ).toHaveText(group.tabs[0][1])
    if (group.path === "/admin/data-ai")
      await expect(
        page
          .getByRole("tablist", { name: group.name, exact: true })
          .getByRole("tab"),
      ).toHaveCount(5)
    for (const [tab, name] of group.tabs) {
      await page.getByRole("tab", { name, exact: true }).click()
      await expect(page).toHaveURL(
        tab === group.tabs[0][0] ? group.path : `${group.path}?tab=${tab}`,
      )
      const panel = page.getByRole("tabpanel", { name, exact: true })
      await expect(panel).toBeVisible()
      if (tab === "approval")
        await expect(
          panel.getByRole("form", { name: "审批规则" }),
        ).toBeVisible()
      else if (tab === "reminders")
        await expect(
          panel.getByRole("region", { name: "持续更新提醒" }),
        ).toBeVisible()
      else
        await expect(
          panel.getByRole("heading", { name, exact: true }),
        ).toBeVisible()
      await expect(
        page
          .getByRole("tablist", { name: group.name, exact: true })
          .getByRole("tab", { selected: true }),
      ).toHaveText(name)
    }
  }
  await page.getByText("更多", { exact: true }).click()
  await page.getByRole("menuitem", { name: "审计日志", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "审计日志", exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "更多", exact: true }).click()
  await page.getByRole("menuitem", { name: "首次设置", exact: false }).click()
  await expect(page).toHaveURL("/setup-wizard")
})

test("direct tab links survive reload and history, invalid tabs use the first", async ({
  page,
}) => {
  test.setTimeout(90000)
  for (const group of groups) {
    for (const [tab, name] of group.tabs) {
      await page.goto(`${group.path}?tab=${tab}`)
      await expect(
        page
          .getByRole("tablist", { name: group.name, exact: true })
          .getByRole("tab", { selected: true }),
      ).toHaveText(name)
    }
    await page.reload()
    await expect(
      page
        .getByRole("tablist", { name: group.name, exact: true })
        .getByRole("tab", { selected: true }),
    ).toHaveText(group.tabs[group.tabs.length - 1][1])
    await page.goto(`${group.path}?tab=unknown`)
    await expect(
      page
        .getByRole("tablist", { name: group.name, exact: true })
        .getByRole("tab", { selected: true }),
    ).toHaveText(group.tabs[0][1])
  }
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/admin/rules?tab=reminders")
  await page.getByRole("tab", { name: "审批规则", exact: true }).click()
  await page.goBack()
  await expect(
    page.getByRole("tab", { name: "定期提醒", exact: true }),
  ).toHaveAttribute("aria-selected", "true")
  await expect(page.getByRole("region", { name: "持续更新提醒" })).toBeVisible()
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
    ["accounts", "/admin/people?tab=accounts"],
    ["roster", "/admin/assistant"],
    ["roles", "/admin/people?tab=roles"],
    ["identities", "/admin/people?tab=identities"],
    ["appeals", "/admin/people?tab=appeals"],
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
    ["progress", "/admin"],
  ]
  await page.goto("/admin/rules?tab=profile-import")
  await expect(page).toHaveURL("/admin/assistant")
  for (const [old, target] of aliases) {
    await page.goto(`/admin/${old}`)
    await expect(page).toHaveURL(target)
  }
})

test("personal settings have only personal tabs and advanced options start collapsed", async ({
  page,
}) => {
  await page.goto("/settings")
  for (const name of ["能力词典", "审批规则"])
    await expect(page.getByRole("tab", { name, exact: true })).toHaveCount(0)
  await page.getByRole("link", { name: "查看企业能力词典" }).click()
  await expect(page).toHaveURL("/admin/rules?tab=dictionary")
  await page.goto("/admin/data-ai?tab=bot")
  await expect(page.getByLabel("聊天原文保留天数")).toBeHidden()
  await page.getByText("高级设置：聊天原文保留", { exact: true }).click()
  await expect(page.getByLabel("聊天原文保留天数")).toBeVisible()
})
