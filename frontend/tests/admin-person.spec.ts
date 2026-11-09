import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("manage an employee from their detail page and see their history", async ({
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
  const accounts = await request.get(`${base}/accounts?q=E12`, { headers })
  const employee = (await accounts.json()).data.find(
    (row: { employee_code: string }) => row.employee_code === "E12",
  )
  expect(employee).toBeTruthy()
  const issued = await request.post(`${base}/accounts/issue`, {
    headers,
    data: { employee_ids: [employee.id], regenerate_pending: true },
  })
  expect(issued.ok()).toBeTruthy()
  await page.goto("/admin/accounts")
  await page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "E12", exact: true }) })
    .getByRole("link", { name: employee.display_name, exact: true })
    .click()
  await expect(page).toHaveURL(new RegExp(`/admin/people/${employee.id}$`))
  await expect(
    page.getByRole("heading", { name: employee.display_name, exact: true }),
  ).toBeVisible()
  await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
    "E12",
  )
  await expect(
    page.getByRole("link", { name: "查看员工画像" }),
  ).toHaveAttribute("href", `/employees/${employee.id}`)
  await expect(
    page.getByRole("heading", { name: "Jira 账号匹配" }),
  ).toBeVisible()
  const history = page.getByRole("region", { name: "修改记录" })
  await expect(history).toContainText("生成账号")
  await page.getByRole("button", { name: "重置密码", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: "确认重置密码" }).click()
  await expect(dialog.getByRole("textbox", { name: "新密码" })).not.toHaveValue(
    "",
  )
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await expect(history).toContainText("重置密码")
  for (const action of ["停用", "启用"]) {
    await page.getByRole("button", { name: action, exact: true }).click()
    await dialog
      .getByRole("button", { name: `确认${action}`, exact: true })
      .click()
    await expect(dialog).toHaveCount(0)
    await expect(history).toContainText(`${action}账号`)
    await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
      action === "停用" ? "已停用" : "待首次登录",
    )
  }
  await page.getByRole("button", { name: "授予角色", exact: true }).click()
  await dialog.getByRole("combobox", { name: "账号", exact: true }).click()
  await page.getByRole("option").click()
  await dialog.getByRole("combobox", { name: "部门", exact: true }).click()
  await page.getByRole("option", { name: "平台研发部", exact: true }).click()
  await dialog.getByRole("button", { name: "确认授予", exact: true }).click()
  await expect(dialog.getByRole("status")).toContainText("角色已授予")
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await page.getByRole("button", { name: "撤销角色", exact: true }).click()
  await dialog.getByRole("button", { name: "确认撤销角色" }).click()
  await expect(history).toContainText("撤销角色")
  await page.reload()
  await expect(history).toContainText("授予角色")
  await expect(history).toContainText("撤销角色")
  await expect(history).toContainText("网页")
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/admin-person.png",
    fullPage: true,
  })
})
