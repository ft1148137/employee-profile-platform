import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("团队负荷在手机横向滑动后仍显示姓名和设置", async ({ page, request }) => {
  const password = "synthetic-mobile-workload-password-2026"
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
    org_name: "合成手机负荷企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const root = admin.access_token
  const settings = await request.put(`${base}/capacity-settings`, {
    headers: { Authorization: `Bearer ${root}` },
    data: { enabled: true },
  })
  expect(settings.ok(), await settings.text()).toBeTruthy()
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${root}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nE,A合成员工,研发,M,e-${crypto.randomUUID()}@example.com\nM,Z合成经理,研发,,m-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await post(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    root,
  )
  const issued = await request.post(`${base}/accounts/issue`, {
    headers: { Authorization: `Bearer ${root}` },
    data: {},
  })
  expect(issued.ok(), await issued.text()).toBeTruthy()
  const accountsResponse = await request.get(`${base}/accounts`, {
    headers: { Authorization: `Bearer ${root}` },
  })
  expect(accountsResponse.ok()).toBeTruthy()
  const accounts: EmployeeAccount[] = (await accountsResponse.json()).data
  const manager = accounts.find((row) => row.employee_code === "M")!
  await post(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    root,
  )
  const reset = await post(
    `${base}/accounts/${manager.account!.user_id}/reset-password`,
    {},
    root,
  )
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok()).toBeTruthy()
  const changed = await request.patch("/api/v1/users/me/password", {
    headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
    data: { current_password: reset.initial_password, new_password: password },
  })
  expect(changed.ok(), await changed.text()).toBeTruthy()
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token: (await changed.json()).access_token, orgId: admin.org_id },
  )

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/capacity?tab=team")
  const hint = page.getByText("左右滑动查看各周和设置", { exact: true })
  await expect(hint).toBeVisible()
  const table = page.getByRole("table", { name: "团队负荷，按部门、姓名排序" })
  await table.scrollIntoViewIfNeeded()
  const firstRow = table.locator("tbody tr").first()
  await expect(
    firstRow.getByRole("rowheader", { name: "A合成员工" }),
  ).toBeVisible()
  const scroller = table.locator("..")
  await scroller.evaluate((element) => {
    element.scrollLeft = element.scrollWidth
  })
  await expect(
    firstRow.getByRole("rowheader", { name: "A合成员工" }),
  ).toBeInViewport()
  const setting = firstRow.getByRole("button", { name: "设置", exact: true })
  await expect(setting).toBeInViewport()
  await setting.click()
  await expect(page.getByRole("dialog")).toContainText("设置周投入 · A合成员工")
  await page.getByRole("dialog").press("Escape")
  await expect(page.getByRole("dialog")).toBeHidden()

  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(hint).toBeHidden()
})
