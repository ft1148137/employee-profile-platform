import { expect, test } from "@playwright/test"
import type { MembershipPublic } from "../src/client"
import {
  mockAdminMessages,
  openAdminAssistant,
  returnToManualPage,
} from "./utils/adminAssistant"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const width of [1440, 390]) {
  test(`admin opens the unified conversation from a manual page at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.setViewportSize({ width, height: 900 })
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({ json: { available: true, reason: "" } }),
    )
    const proposalId = crypto.randomUUID()
    const changeSet = {
      id: crypto.randomUUID(),
      revision: 1,
      status: "applied",
      undo_of_id: null,
      items: [
        {
          function: "dismiss_capability_proposal",
          title: "忽略起草时提议的新能力（可撤销）",
          args: { id: proposalId },
          before: {
            id: proposalId,
            name: "合成能力建议",
            status: "open",
            updated_at: "2026-01-01T00:00:00Z",
          },
          after: {
            id: proposalId,
            name: "合成能力建议",
            status: "dismissed",
            updated_at: "2026-01-02T00:00:00Z",
          },
          risk: "normal",
          undoable: true,
        },
      ],
    }
    await page.route(
      `**/admin-assistant/change-sets/${changeSet.id}*`,
      (route) => route.fulfill({ json: changeSet }),
    )
    const sent: string[] = []
    const saveReply = await mockAdminMessages(page)
    await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
      sent.push(route.request().postDataJSON().page)
      const reply = {
        messages: [
          { type: "text", text: "请查看首页待办。" },
          { type: "admin_change_set", change_set: changeSet },
          { type: "link", title: "打开管理首页", page: "/admin" },
        ],
      }
      saveReply(route.request().url(), reply)
      await route.fulfill({
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ type: "final", response: reply })}\n`,
      })
    })
    for (const current of [
      "/admin/people?tab=roster",
      "/admin/data-ai?tab=bot",
    ]) {
      await page.goto(current)
      await openAdminAssistant(page)
      const dialog = page.getByRole("region", { name: "管理助手", exact: true })
      await expect(
        dialog.getByRole("heading", { name: "管理助手" }),
      ).toBeVisible()
      await dialog
        .getByRole("textbox", { name: "发给管理助手的消息" })
        .fill("今天有什么要处理的")
      await dialog.getByRole("button", { name: "发送", exact: true }).click()
      await expect(
        dialog.getByText("请查看首页待办。", { exact: true }).last(),
      ).toBeVisible()
      await expect(
        dialog.getByRole("link", { name: "打开管理首页" }).last(),
      ).toHaveAttribute("href", "/admin")
      expect(sent[sent.length - 1]).toBe("/admin")
      await expect(
        dialog.getByText("忽略「合成能力建议」", { exact: true }).last(),
      ).toBeVisible()
      await expectChineseCopy(page)
      await page.screenshot({
        path: testInfo.outputPath(`admin-assistant-${sent.length}.png`),
      })
      await returnToManualPage(page)
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
    let modelAvailable = available
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({
        json: {
          available: modelAvailable,
          reason: modelAvailable ? "" : "请先配置模型，可以继续使用手工页面。",
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
        page
          .getByRole("status")
          .filter({ hasText: "请先配置模型，可以继续使用手工页面。" })
          .getByRole("link", { name: "去配置模型" }),
      ).toHaveAttribute("href", "/admin/data-ai?tab=model")
    } else {
      await expect(
        page.getByRole("link", { name: "首页", exact: true }),
      ).toBeVisible()
    }
    let sends = 0
    const saveReply = await mockAdminMessages(page)
    await page.route("**/admin-assistant/sessions/*/turns?*", (route) => {
      sends++
      const reply = {
        messages: [
          {
            type: "table",
            title: "账号列表",
            queried_at: "2026-11-11T00:25:00Z",
            columns: ["display_name", "employment_status", "roles"],
            rows: [
              [
                "合成员工",
                "inactive",
                [{ display_name: "合成员工", role: "dept_manager" }],
              ],
              ["active", "active", []],
            ],
            total: 2,
            page: "/admin/people?tab=accounts",
          },
        ],
      }
      saveReply(route.request().url(), reply)
      return route.fulfill({
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ type: "final", response: reply })}\n`,
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
    if (!available) {
      await expect(page.getByLabel("发给管理助手的消息")).toBeDisabled()
      modelAvailable = true
      await page.reload()
    }
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      page.getByRole("cell", { name: "已离职", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "active", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "部门经理", exact: true }),
    ).toBeVisible()
    await expect(page.getByText(/查询于 \d{2}:\d{2}/)).toBeVisible()
    expect(sends).toBe(1)
    await page.getByRole("link", { name: "新对话", exact: true }).click()
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
