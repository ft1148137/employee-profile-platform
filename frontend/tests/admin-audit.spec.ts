import { expect, test } from "@playwright/test"
import { localMidnight } from "../src/components/Admin/auditLabels"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("audit records show account and revoked role labels with filters", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await request.get("/api/v1/me/memberships", { headers })
  const orgId = (await memberships.json())[0].org.id
  const base = `/api/v1/orgs/${orgId}`
  const accounts = await request.get(`${base}/accounts?q=E11`, { headers })
  const employee = (await accounts.json()).data.find(
    (e: { employee_code: string }) => e.employee_code === "E11",
  )
  expect(employee).toBeTruthy()
  const issued = await request.post(`${base}/accounts/issue`, {
    headers,
    data: { employee_ids: [employee.id], regenerate_pending: true },
  })
  expect(issued.ok()).toBeTruthy()
  const updated = await request.get(`${base}/accounts?q=E11`, { headers })
  const userId = (await updated.json()).data.find(
    (e: { employee_code: string }) => e.employee_code === "E11",
  ).account.user_id
  for (const action of ["reset-password", "disable", "enable"]) {
    const response = await request.post(
      `${base}/accounts/${userId}/${action}`,
      { headers },
    )
    expect(response.ok()).toBeTruthy()
  }
  const departments = await request.get(`${base}/departments`, { headers })
  const department = (await departments.json()).data.find(
    (d: { name: string }) => d.name === "平台研发部",
  )
  const granted = await request.post(`${base}/role-grants`, {
    headers,
    data: {
      user_id: userId,
      role: "dept_manager",
      department_id: department.id,
    },
  })
  expect(granted.ok()).toBeTruthy()
  const revoked = await request.delete(
    `${base}/role-grants/${(await granted.json()).id}`,
    { headers },
  )
  expect(revoked.ok()).toBeTruthy()
  await page.getByText("更多", { exact: true }).click()
  await page.getByRole("link", { name: "审计日志" }).click()
  const rows = page
    .getByRole("row")
    .filter({ hasText: "（employee11@demo.example.com）" })
  await expect(rows.nth(5)).toBeVisible()
  for (const action of [
    "生成账号",
    "重置密码",
    "停用账号",
    "启用账号",
    "授予角色",
    "撤销角色",
  ]) {
    const row = rows.filter({
      has: page.getByRole("cell", { name: action, exact: true }),
    })
    await expect(row.first()).toContainText(
      "企业管理员（admin@demo.example.com）",
    )
  }
  await expect(rows.first()).toContainText("撤销角色")
  await expect(rows.nth(1)).toContainText("授予角色")
  await expect(rows.filter({ hasText: "撤销角色" }).first()).toContainText(
    "部门经理 · 平台研发部",
  )
  await expectChineseCopy(page, ["admin"])
  await page.screenshot({
    path: "test-results/admin-audit.png",
    fullPage: true,
  })
  await page.getByRole("combobox", { name: "操作类型筛选" }).click()
  await page.getByRole("option", { name: "角色", exact: true }).click()
  await expect(rows.nth(1)).toBeVisible()
  for (const row of await rows.all())
    await expect(row).toContainText(/授予角色|撤销角色/)
  const yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  const date = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`
  await page.getByLabel("结束日期（含当天）").fill(date)
  await expect(rows).toHaveCount(0)
  await expectChineseCopy(page, ["admin"])
})

test("date filters use local calendar boundaries", () => {
  expect(localMidnight("")).toBeUndefined()
  const start = new Date(localMidnight("2026-03-08")!)
  const end = new Date(localMidnight("2026-03-08", true)!)
  expect([start.getDate(), start.getHours()]).toEqual([8, 0])
  expect([end.getDate(), end.getHours()]).toEqual([9, 0])
})
