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
      "/admin/people?tab=accounts",
      "/admin/data-ai?tab=bot",
    ]) {
      await page.goto(current)
      await openAdminAssistant(page)
      const dialog = page.getByRole("region", { name: "管理助手", exact: true })
      await expect(
        page.getByRole("heading", { name: "管理助手", exact: true }),
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
      expect(sent[sent.length - 1]).toBe("/admin/assistant")
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

test("employee manual pages have no floating assistants", async ({ page }) => {
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
  ).toHaveCount(0)
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
          reason: modelAvailable
            ? ""
            : "请先配置模型，你也可以直接在对应页面操作。",
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
    await expect(page).toHaveURL(/\/assistant\?mode=admin$/)
    if (!available) {
      await expect(page.getByRole("log")).toContainText(
        "请先配置模型，你也可以直接在对应页面操作。",
      )
      await expect(
        page
          .getByRole("status")
          .filter({ hasText: "请先配置模型，你也可以直接在对应页面操作。" })
          .getByRole("link", { name: "去配置模型" }),
      ).toHaveAttribute("href", "/admin/data-ai?tab=model")
    }
    await expect(
      page.getByRole("link", { name: "首页", exact: true }),
    ).toHaveCount(0)
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
      .getByRole("button", { name: "让助手处理", exact: true })
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
    await page.getByRole("button", { name: "新对话", exact: true }).click()
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

test("demand table shows Chinese labels and opens demand details", async ({
  page,
}) => {
  const demands = [
    { status: "submitted", label: "待答复", tab: "incoming" },
    { status: "replied", label: "已答复", tab: "outgoing" },
    { status: "accepted", label: "已接受", tab: "outgoing" },
    { status: "withdrawn", label: "已撤回", tab: "outgoing" },
  ].map((demand, index) => ({
    ...demand,
    id: crypto.randomUUID(),
    capability: `合成能力 ${index + 1}`,
  }))
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const saveReply = await mockAdminMessages(page)
  await page.route("**/admin-assistant/sessions/*/turns?*", (route) => {
    const reply = {
      messages: [
        {
          type: "table",
          title: "部门需求列表",
          page: "/capacity",
          total: demands.length,
          columns: [
            "id",
            "can_reply",
            "capability_name",
            "headcount",
            "allocation_percent",
            "window_start",
            "window_end",
            "status",
            "created_at",
          ],
          rows: demands.map(({ id, tab, capability, status }) => [
            id,
            tab === "incoming",
            capability,
            2,
            50,
            "2026-12-01",
            "2026-12-31",
            status,
            "2026-11-20T00:00:00Z",
          ]),
        },
      ],
    }
    saveReply(route.request().url(), reply)
    return route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: reply })}\n`,
    })
  })
  await page.goto("/admin/assistant?prompt=查看部门需求")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const table = page.getByRole("table")
  await expect(table.getByRole("columnheader")).toHaveText([
    "能力",
    "人数",
    "投入占比",
    "开始日期",
    "结束日期",
    "状态",
    "创建时间",
  ])
  for (const { capability, id, label, tab } of demands) {
    const row = table.getByRole("row").filter({ hasText: capability })
    await expect(row.getByRole("cell")).toHaveText([
      capability,
      "2",
      "50%",
      "2026-12-01",
      "2026-12-31",
      label,
      "2026-11-20T00:00:00Z",
    ])
    await expect(row.getByRole("link", { name: capability })).toHaveAttribute(
      "href",
      `/capacity?tab=${tab}&demand=${id}`,
    )
  }
  for (const { capability, id, tab } of demands.slice(0, 2)) {
    await table.getByRole("link", { name: capability }).click()
    await expect(page).toHaveURL(`/capacity?tab=${tab}&demand=${id}`)
    await page.goBack()
  }
})

test("manager with employee identity lands on assistant", async ({ page }) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL("/assistant")
})
