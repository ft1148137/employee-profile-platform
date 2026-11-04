import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("grant and revoke a department manager, preserve the last administrator", async ({
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
  const accounts = await request.get(`${base}/accounts?q=E10`, { headers })
  const employee = (await accounts.json()).data[0]
  expect(employee.employee_code).toBe("E10")
  if (!employee.account) {
    const issued = await request.post(`${base}/accounts/issue`, {
      headers,
      data: { employee_ids: [employee.id] },
    })
    expect(issued.ok()).toBeTruthy()
  } else {
    expect(
      (
        await request.post(
          `${base}/accounts/${employee.account.user_id}/enable`,
          { headers },
        )
      ).ok(),
    ).toBeTruthy()
  }
  for (const grant of employee.roles) {
    expect(
      (
        await request.delete(`${base}/role-grants/${grant.id}`, { headers })
      ).ok(),
    ).toBeTruthy()
  }
  await page.getByRole("link", { name: "角色", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "角色", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("cell", { name: "admin@demo.example.com", exact: true }),
  ).toBeVisible()
  await expectChineseCopy(page, ["admin", "superadmin"])
  await page.screenshot({
    path: "test-results/admin-roles.png",
    animations: "disabled",
  })
  await page.getByRole("button", { name: "授予角色", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("button", { name: "确认授予" })).toBeDisabled()
  await dialog.getByRole("textbox", { name: "搜索姓名或工号" }).fill("E10")
  await dialog.getByRole("combobox", { name: "账号", exact: true }).click()
  await page
    .getByRole("option", { name: /employee10@demo.example.com/i })
    .click()
  await expect(dialog.getByRole("button", { name: "确认授予" })).toBeDisabled()
  await dialog.getByRole("combobox", { name: "部门", exact: true }).click()
  await page.getByRole("option", { name: "平台研发部", exact: true }).click()
  await expect(page.getByRole("listbox")).toBeHidden()
  await expectChineseCopy(page, ["admin", "superadmin"])
  await page.screenshot({
    path: "test-results/admin-grant-role.png",
    animations: "disabled",
  })
  await dialog.getByRole("button", { name: "确认授予" }).click()
  await expect(dialog.getByRole("status")).toHaveText("角色已授予")
  await dialog.getByRole("button", { name: "确认授予" }).click()
  await expect(dialog.getByRole("status")).toHaveText("这个角色已经授予过")
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  const row = page.getByRole("row").filter({
    has: page.getByRole("cell", {
      name: "employee10@demo.example.com",
      exact: true,
    }),
  })
  await expect(row).toContainText("部门经理")
  await expect(row).toContainText("平台研发部")
  await row.getByRole("button", { name: "撤销", exact: true }).click()
  await expect(dialog).toContainText("平台研发部 的部门经理")
  await expectChineseCopy(page, ["admin", "superadmin"])
  await dialog.getByRole("button", { name: "确认撤销" }).click()
  await expect(row).toHaveCount(0)
  // The demo has two effective admins; backend tests cover the last-admin rule.
  const revokePattern = "**/api/v1/orgs/*/role-grants/*"
  await page.route(revokePattern, (route) =>
    route.request().method() === "DELETE"
      ? route.fulfill({
          status: 409,
          json: { detail: "不能移除最后一名有效的企业管理员" },
        })
      : route.continue(),
  )
  const admin = page.getByRole("row").filter({
    has: page.getByRole("cell", {
      name: "admin@demo.example.com",
      exact: true,
    }),
  })
  await admin.getByRole("button", { name: "撤销", exact: true }).click()
  await dialog.getByRole("button", { name: "确认撤销" }).click()
  await expect(dialog.getByRole("alert")).toContainText(
    "不能移除最后一名有效的企业管理员",
  )
  await expect(dialog).toBeVisible()
  await expectChineseCopy(page, ["admin", "superadmin"])
  await page.unroute(revokePattern)
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  await page.reload()
  await expect(admin).toContainText("企业管理员")
})
