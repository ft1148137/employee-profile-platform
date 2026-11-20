import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("经理在团队负荷页看到当前参考和正式答复的口径说明", async ({
  page,
  request,
}) => {
  const password = "synthetic-m62-notice-password-2026"
  const api = async (
    url: string,
    data: object,
    token?: string,
    method = "POST",
  ) => {
    const response = await request.fetch(url, {
      method,
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const setup = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成口径企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const root = admin.access_token
  await api(`${base}/capacity-settings`, { enabled: true }, root, "PUT")
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${root}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nM,合成口径经理,研发,,m-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  await api(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    root,
  )
  const issued = await request.post(`${base}/accounts/issue`, {
    headers: { Authorization: `Bearer ${root}` },
    data: {},
  })
  expect(issued.ok()).toBeTruthy()
  const response = await request.get(`${base}/accounts`, {
    headers: { Authorization: `Bearer ${root}` },
  })
  expect(response.ok()).toBeTruthy()
  const accounts: EmployeeAccount[] = (await response.json()).data
  const manager = accounts.find((row) => row.employee_code === "M")!
  await api(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    root,
  )
  const reset = await api(
    `${base}/accounts/${manager.account!.user_id}/reset-password`,
    {},
    root,
  )
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok()).toBeTruthy()
  const changed = await api(
    "/api/v1/users/me/password",
    { current_password: reset.initial_password, new_password: password },
    (await login.json()).access_token,
    "PATCH",
  )
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token: changed.access_token, orgId: admin.org_id },
  )
  await page.goto("/capacity")
  await page.getByRole("link", { name: "团队负荷", exact: true }).click()
  const help = page.getByRole("button", { name: "团队负荷说明和图例" })
  await help.click()
  await expect(page.getByRole("tooltip")).toContainText(
    "展示未来 8 周的投入参考，实际承接以部门经理答复为准。",
  )
  await help.press("Escape")
  const chart = page.getByRole("table", { name: "团队负荷，按部门、姓名排序" })
  const dates = chart.locator("thead time")
  await expect(dates).toHaveCount(8)
  const current = (await dates.first().getAttribute("datetime"))!
  const shift = (weeks: number) => {
    const date = new Date(current)
    date.setUTCDate(date.getUTCDate() + 7 * weeks)
    return date.toISOString().slice(0, 10)
  }
  await page.getByRole("button", { name: "上一期" }).click()
  await expect(dates.first()).toHaveAttribute("datetime", shift(-4))
  await expect(chart.getByText("本周", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "回到本周" }).click()
  await expect(dates.first()).toHaveAttribute("datetime", current)
  await page.getByRole("button", { name: "下一期" }).click()
  await expect(dates.first()).toHaveAttribute("datetime", shift(4))
  await expect(chart.getByText("本周", { exact: true })).toHaveCount(0)
})
