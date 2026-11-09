import { expect, test } from "@playwright/test"
import type { MembershipPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const width of [1440, 390]) {
  test(`admin sidebar carries the current page at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.setViewportSize({ width, height: 900 })
    const sent: string[] = []
    await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
      sent.push(route.request().postDataJSON().page)
      await route.fulfill({
        json: {
          messages: [
            { type: "text", text: "请查看首页待办。" },
            { type: "link", title: "打开管理首页", page: "/admin" },
          ],
        },
      })
    })
    for (const current of [
      "/admin/people?tab=roster",
      "/admin/data-ai?tab=bot",
    ]) {
      await page.goto(current)
      await page.getByRole("button", { name: "打开管理助手" }).click()
      const dialog = page.getByRole("dialog")
      await expect(
        dialog.getByRole("heading", { name: "管理助手" }),
      ).toBeVisible()
      await dialog
        .getByRole("textbox", { name: "发给管理助手的消息" })
        .fill("今天有什么要处理的")
      await dialog.getByRole("button", { name: "发送", exact: true }).click()
      await expect(
        dialog.getByText("请查看首页待办。", { exact: true }),
      ).toBeVisible()
      await expect(
        dialog.getByRole("link", { name: "打开管理首页" }),
      ).toHaveAttribute("href", "/admin")
      expect(sent[sent.length - 1]).toBe(current)
      await expectChineseCopy(page)
      await page.screenshot({
        path: testInfo.outputPath(`admin-assistant-${sent.length}.png`),
      })
      await dialog.getByRole("button", { name: "关闭", exact: true }).click()
    }
    expect(sent).toHaveLength(2)
    await page.goto("/employees")
    await expect(
      page.getByRole("button", { name: "打开管理助手" }),
    ).toHaveCount(0)
  })
}

test("employee sees their profile bot on a manual page", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules")
  await expect(page.getByRole("button", { name: "打开管理助手" })).toHaveCount(
    0,
  )
  await expect(
    page.getByRole("button", { name: "打开画像机器人" }),
  ).toBeVisible()
})

for (const available of [true, false]) {
  test(`admin lands on assistant when available=${available}`, async ({
    page,
  }, testInfo) => {
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({
        json: {
          available,
          reason: available ? "" : "请先配置模型，可以继续使用手工页面。",
        },
      }),
    )
    await page.route("**/me/memberships", async (route) => {
      const data: MembershipPublic[] = await (await route.fetch()).json()
      await route.fulfill({
        json: data.map((membership) => ({
          ...membership,
          roles: [...membership.roles, "dept_manager", "employee"],
        })),
      })
    })
    await page.route("**/orgs/*/setup", async (route) => {
      const data = await (await route.fetch()).json()
      await route.fulfill({ json: { ...data, completed: true } })
    })
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(page).toHaveURL(
      available ? /\/admin\/assistant$/ : /\/admin\/?$/,
    )
    if (!available) {
      await expect(
        page.getByText("请先配置模型，可以继续使用手工页面。", { exact: true }),
      ).toBeVisible()
      await expect(
        page.getByRole("link", { name: "去配置模型" }),
      ).toHaveAttribute("href", "/admin/data-ai?tab=model")
    } else {
      await expect(
        page.getByRole("link", { name: "切换到手工页面" }),
      ).toBeVisible()
    }
    let sends = 0
    await page.route("**/admin-assistant/sessions/*/turns?*", (route) => {
      sends++
      return route.fulfill({
        json: {
          messages: [
            {
              type: "table",
              title: "账号列表",
              columns: ["display_name", "employment_status"],
              rows: [
                ["合成员工", "inactive"],
                ["active", "active"],
              ],
              total: 2,
              page: "/admin/people?tab=accounts",
            },
          ],
        },
      })
    })
    await page.goto("/admin")
    const action = page
      .getByRole("link", { name: "让助手处理", exact: true })
      .first()
    await action.click()
    await expect(
      page.getByRole("textbox", { name: "发给管理助手的消息" }),
    ).not.toHaveValue("")
    await page.goto("/admin/assistant?prompt=查看账号")
    await expect(
      page.getByRole("textbox", { name: "发给管理助手的消息" }),
    ).toHaveValue("查看账号")
    expect(sends).toBe(0)
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      page.getByRole("cell", { name: "已离职", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "active", exact: true }),
    ).toBeVisible()
    expect(sends).toBe(1)
    await page
      .getByRole("button", { name: "让助手处理", exact: true })
      .first()
      .click()
    await expect(
      page.getByRole("textbox", { name: "发给管理助手的消息" }),
    ).not.toHaveValue("")
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(
      page.getByRole("button", { name: "发送", exact: true }),
    ).toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath("full-screen-mobile.png"),
    })
    await expectChineseCopy(page, ["active"])
  })
}

test("manager keeps employee landing page", async ({ page }) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL("/employees")
})
