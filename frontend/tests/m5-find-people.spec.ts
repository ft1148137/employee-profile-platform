import { expect, type Page, test } from "@playwright/test"
import type { PeopleSearchRow } from "../src/client"
import { mockAdminMessages } from "./utils/adminAssistant"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
test.use({ storageState: { cookies: [], origins: [] } })

function sessionsPath(admin: boolean) {
  return `**/${admin ? "admin-assistant" : "bot"}/sessions?*`
}

function mockModelAvailability(page: Page, admin: boolean, available: boolean) {
  return page.route(sessionsPath(admin), async (route) => {
    if (route.request().method() !== "GET") return route.fallback()
    const response = await route.fetch()
    return route.fulfill({
      response,
      json: { ...(await response.json()), model_available: available },
    })
  })
}

for (const admin of [false, true]) {
  test(`${admin ? "administrator" : "manager"} describes ability and minimum level to find a profile`, async ({
    page,
    request,
  }) => {
    await logInUser(
      page,
      admin ? "admin@demo.example.com" : "manager.platform@demo.example.com",
      password,
    )
    const token = await page.evaluate(() =>
      localStorage.getItem("access_token"),
    )
    const headers = { Authorization: `Bearer ${token}` }
    const memberships = await (
      await request.get("/api/v1/me/memberships", { headers })
    ).json()
    const base = `/api/v1/orgs/${memberships[0].org.id}`
    const dictionary = await (
      await request.get(`${base}/capabilities`, { headers })
    ).json()
    const capability = dictionary.data[0]
    const employees = await (
      await request.get(`${base}/employees`, { headers })
    ).json()
    const employee = employees.data[0]
    // Exercise each assistant response contract without changing the shared demo model settings.
    const row: PeopleSearchRow = {
      employee_id: employee.id,
      display_name: employee.display_name,
      employee_code: employee.employee_code,
      department_name: employee.department.name,
      capability_key: capability.key,
      capability_name: capability.name,
      statement: "合成判断：能独立完成复杂任务",
      level: "independent_complex",
      level_name: "能独立完成复杂任务",
      status: "confirmed",
      evidence_count: 2,
      latest_evidence_at: "2026-11-01T00:00:00Z",
      published_by: { actor_type: "user", display_name: "合成批准人" },
      published_at: "2026-11-02T00:00:00Z",
    }
    const prompt = `帮我找具备「${capability.name}」且最低等级为「${row.level_name}」的员工`
    await mockModelAvailability(page, admin, true)
    if (admin) {
      await page.route("**/admin-assistant/availability?*", (route) =>
        route.fulfill({ json: { available: true, reason: "" } }),
      )
    }
    const saveReply = admin ? await mockAdminMessages(page) : undefined
    await page.route(
      `**/${admin ? "admin-assistant" : "bot"}/sessions/*/turns?*`,
      (route) => {
        expect(route.request().postDataJSON().text).toBe(prompt)
        const messages = admin
          ? [
              {
                type: "table",
                title: "按能力找人",
                columns: Object.keys(row),
                rows: [Object.values(row)],
                total: 1,
                page: "/employees",
              },
            ]
          : [
              {
                type: "text",
                text: `[${row.display_name}](/employees/${row.employee_id})：${row.statement}，等级：${row.level_name}，批准人：合成批准人`,
              },
            ]
        saveReply?.(route.request().url(), { messages })
        return route.fulfill({
          contentType: "application/x-ndjson",
          body: `${JSON.stringify({ type: "final", response: { messages } })}\n`,
        })
      },
    )
    if (!admin) {
      const settings = await request.get(`${base}/capacity-settings`, {
        headers,
      })
      expect(settings.ok()).toBeTruthy()
      expect((await settings.json()).enabled).toBe(false)
    }
    const sessions = page.waitForResponse(sessionsPath(admin))
    await page.goto("/find-people")
    await expect(page).toHaveURL(
      `/assistant?mode=${admin ? "admin" : "personal"}&find=true`,
    )
    await expect(
      page.getByRole("heading", { name: "按能力找人", exact: true }),
    ).toBeVisible()
    await sessions
    await expect(
      page.getByRole("form", { name: "按能力找人（只读筛选）" }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("navigation", { name: "助手模式" }),
    ).toHaveCount(0)
    await expect(page.getByRole("heading", { name: "最近会话" })).toBeVisible()
    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
    await page
      .getByLabel(admin ? "发给管理助手的消息" : "发给机器人的消息")
      .fill(prompt)
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const results = page.getByRole("log")
    await expect(results).toContainText(row.statement)
    await expect(results).toContainText("合成批准人")
    if (admin) {
      await expect(results.getByRole("columnheader")).toHaveText([
        "姓名",
        "工号",
        "部门",
        "能力",
        "能力判断",
        "等级",
        "状态",
        "证据数",
        "最近证据时间",
        "批准人",
        "发布时间",
      ])
      await expect(results).not.toContainText(row.employee_id)
    }
    await page.screenshot({
      path: test.info().outputPath("find-people.png"),
      fullPage: true,
    })
    await results
      .getByRole("link", { name: employee.display_name, exact: true })
      .click()
    await expect(page).toHaveURL(new RegExp(`/employees/${employee.id}$`))
    await expect(
      page.getByRole("heading", { name: employee.display_name, exact: true }),
    ).toBeVisible()
  })

  test(`${admin ? "administrator" : "manager"} searches published profiles within scope with the model disabled`, async ({
    page,
  }) => {
    await logInUser(
      page,
      admin ? "admin@demo.example.com" : "manager.platform@demo.example.com",
      password,
    )
    await mockModelAvailability(page, admin, false)
    const form = page.getByRole("form", { name: "按能力找人（只读筛选）" })
    const openFallback = page.getByText(
      "模型暂未启用 · 按能力找人（只读筛选）",
      {
        exact: true,
      },
    )
    if (!admin) {
      await page.goto("/assistant")
      await expect(openFallback).toBeVisible()
      await expect(form).toBeHidden()
      await openFallback.click()
      await expect(form).toBeVisible()
    }
    await page.goto("/find-people")
    await expect(page).toHaveURL(
      `/assistant?mode=${admin ? "admin" : "personal"}&find=true`,
    )
    await expect(form).toBeVisible()
    const search = form.getByRole("button", { name: "查找", exact: true })
    const ability = form.getByRole("combobox", { name: "能力（必选）" })
    const department = form.getByRole("combobox", { name: "部门（含下级）" })
    const minimum = form.getByRole("combobox", { name: "最低等级" })
    const results = page.getByRole("region", { name: "找人结果" })
    await expect(search).toBeDisabled()
    await expect(minimum).toBeDisabled()

    // Use the real search API and the demo enterprise's published judgments.
    await ability.selectOption({ label: "模型服务化" })
    await search.click()
    const otherDepartment = results
      .getByRole("row")
      .filter({ hasText: "算法与模型部" })
    if (admin) {
      await expect(
        otherDepartment.getByRole("link", { name: "张伟" }),
      ).toBeVisible()
    } else {
      await expect(otherDepartment).toHaveCount(0)
      await expect(results).toContainText("未知不等于不会")
    }

    await ability.selectOption({ label: "后端 API 开发" })
    await minimum.selectOption({ label: "能独立完成复杂任务" })
    await department.selectOption({ label: "平台研发部" })
    await expect(
      department.locator("option").filter({ hasText: "算法与模型部" }),
    ).toHaveCount(admin ? 1 : 0)
    await form
      .getByRole("combobox", { name: "判断状态" })
      .selectOption({ label: "已确认" })
    await search.click()
    await expect(results).toContainText("共 1 条已发布判断")
    await expect(results).toContainText("通过复杂冲突处理任务与经理复核")
    await expect(results.getByRole("link", { name: "林澈" })).toHaveCount(0)
    const refreshed = page.waitForResponse("**/people-search?*")
    await search.click()
    const response = await refreshed
    expect(response.ok()).toBeTruthy()
    const filters = new URL(response.url()).searchParams
    expect(filters.get("minimum_level")).toBe("independent_complex")
    expect(filters.get("department_id")).toBeTruthy()
    expect(filters.getAll("statuses")).toEqual(["confirmed"])
    const profile = results.getByRole("link", { name: "张伟", exact: true })
    await expect(profile).toHaveAttribute("href", /^\/employees\/[\w-]+$/)
    await profile.click()
    await expect(
      page.getByRole("heading", { name: "张伟", exact: true }),
    ).toBeVisible()
  })
}

test("manager without an employee account can still open the read-only search", async ({
  page,
}) => {
  await logInUser(page, "manager.platform@demo.example.com", password)
  await page.route("**/me/memberships", async (route) => {
    const response = await route.fetch()
    const memberships = await response.json()
    memberships[0].employee_id = null
    return route.fulfill({ response, json: memberships })
  })
  await mockModelAvailability(page, false, false)
  await page.goto("/find-people")
  await expect(
    page.getByRole("form", { name: "按能力找人（只读筛选）" }),
  ).toBeVisible()
  await expect(
    page.getByRole("alert").filter({ hasText: "当前账号没有员工身份" }),
  ).toBeVisible()
})

for (const available of [false, true]) {
  test(`employee has no find-people entry or fallback with the model ${available ? "enabled" : "disabled"}`, async ({
    page,
  }) => {
    await logInUser(page, "employee.platform@demo.example.com", password)
    await mockModelAvailability(page, false, available)
    const sessions = page.waitForResponse(sessionsPath(false))
    await page.goto("/assistant?find=true")
    await sessions
    await expect(page.getByRole("heading", { name: "最近会话" })).toBeVisible()
    await expect(
      page.getByRole("link", { name: "找人", exact: true }),
    ).toHaveCount(0)
    await expect(
      page.getByText("模型暂未启用 · 按能力找人（只读筛选）", { exact: true }),
    ).toHaveCount(0)
    await page.goto("/find-people")
    await expect(page.getByRole("alert")).toContainText("没有权限使用找人")
    await expect(
      page.getByRole("combobox", { name: "能力（必选）" }),
    ).toHaveCount(0)
  })
}
