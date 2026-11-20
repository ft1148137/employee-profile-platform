import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

for (const trigger of ["focus", "operation", "query"] as const) {
  test(`关闭模块后员工页面及时更新：${trigger}`, async ({ page, request }) => {
    const password = "synthetic-disabled-password-2026"
    const post = async (url: string, data: object, token?: string) => {
      const response = await request.post(url, {
        data,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      expect(response.ok(), await response.text()).toBeTruthy()
      return response.json()
    }
    const setup = await post("/api/v1/private/setup-tokens", {
      purpose: "new_org",
    })
    const admin = await post("/api/v1/setup/initialize", {
      token: setup.token,
      org_name: "合成模块关闭企业",
      admin_username: `disabled-${crypto.randomUUID()}@example.com`,
      admin_password: password,
    })
    const base = `/api/v1/orgs/${admin.org_id}`
    const headers = { Authorization: `Bearer ${admin.access_token}` }
    const roster = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,上级工号,邮箱\nE,合成员工,研发,,disabled-e-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
    expect(roster.ok()).toBeTruthy()
    const preview = await roster.json()
    await post(
      `${base}/roster/imports/${preview.id}/apply`,
      {
        base_revision: preview.base_revision,
      },
      admin.access_token,
    )
    const issued = await request.post(`${base}/accounts/issue`, {
      headers,
      data: {},
    })
    expect(issued.ok()).toBeTruthy()
    const accounts: EmployeeAccount[] = (
      await (await request.get(`${base}/accounts`, { headers })).json()
    ).data
    const employee = accounts.find((row) => row.employee_code === "E")!
    const reset = await post(
      `${base}/accounts/${employee.account!.user_id}/reset-password`,
      {},
      admin.access_token,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    const token = (await changed.json()).access_token
    const setEnabled = async (enabled: boolean) => {
      const response = await request.put(`${base}/capacity-settings`, {
        headers,
        data: { enabled, requester_policy: "everyone" },
      })
      expect(response.ok(), await response.text()).toBeTruthy()
    }
    if (trigger === "focus") {
      await setEnabled(false)
      await page.goto("/login")
      await page.evaluate(
        ({ token, orgId }) => {
          localStorage.setItem("access_token", token)
          localStorage.setItem("current_org_id", orgId)
        },
        { token: admin.access_token, orgId: admin.org_id },
      )
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/admin/rules?tab=capacity")
      const form = page.getByRole("form", { name: "负荷与需求规则" })
      const summary = page.getByRole("region", { name: "当前生效摘要" })
      await expect(summary).toContainText("已关闭")
      await expect(page.getByRole("region", { name: "影响范围" })).toBeVisible()
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(375)
      await form.getByRole("checkbox", { name: "启用规划与负荷功能" }).check()
      await expect(summary).toContainText("已关闭")
      await form.getByRole("button", { name: "保存设置" }).click()
      await expect(summary).toContainText("已启用")
      await page.setViewportSize({ width: 1280, height: 720 })
    } else {
      await setEnabled(true)
      await page.goto("/login")
    }
    await page.evaluate(
      ({ token, orgId }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", orgId)
      },
      { token, orgId: admin.org_id },
    )
    await page.goto("/capacity")
    const menu = page.getByRole("link", { name: "规划", exact: true })
    await expect(menu).toBeVisible()
    const adjustment = page.getByRole("dialog", { name: "申请调整" })
    const openAdjustment = async () => {
      await page.getByRole("button", { name: "申请调整", exact: true }).click()
      await expect(adjustment.getByLabel("申请负荷（%）")).toBeVisible()
    }
    await openAdjustment()
    if (trigger === "query")
      await adjustment.getByRole("button", { name: "关闭" }).click()
    if (trigger === "operation") {
      const endpoint = `${base}/capacity/workloads/me/requests`
      await page.route(`**${endpoint}`, (route) =>
        route.fulfill({
          status: 404,
          json: { detail: "未找到该员工或申请" },
        }),
      )
      await adjustment.getByLabel("申请负荷（%）").fill("60")
      await adjustment.getByLabel("申请原因").fill("合成普通错误")
      await adjustment
        .getByRole("button", { name: "提交申请", exact: true })
        .click()
      await expect(adjustment.getByRole("alert")).toContainText(
        "未找到该员工或申请",
      )
      await adjustment.getByRole("button", { name: "关闭" }).click()
      await expect(menu).toBeVisible()
      await page.unroute(`**${endpoint}`)
      await openAdjustment()
    }
    if (trigger === "query")
      await page.getByRole("link", { name: "设置", exact: true }).click()
    await setEnabled(false)
    if (trigger === "focus") {
      await page.evaluate(() =>
        document.dispatchEvent(
          new Event("visibilitychange", { bubbles: true }),
        ),
      )
    } else if (trigger === "operation") {
      await adjustment.getByLabel("申请负荷（%）").fill("60")
      await adjustment.getByLabel("申请原因").fill("合成关闭后操作")
      await adjustment
        .getByRole("button", { name: "提交申请", exact: true })
        .click()
    } else {
      await menu.click()
    }
    await expect(adjustment).toHaveCount(0)
    if (trigger === "focus") await expect(menu).toHaveCount(0)
    await expect(page.getByRole("status")).toHaveText(
      "规划与负荷功能未开启，如需使用请联系企业管理员。",
    )
    await expect(menu).toHaveCount(0)
    await expect(page.getByRole("alert")).toHaveCount(0)
    await setEnabled(true)
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange", { bubbles: true })),
    )
    await expect(menu).toBeVisible()
    await page.getByRole("link", { name: "我的负荷", exact: true }).click()
    await openAdjustment()
  })
}
