import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("reset password once, disable and enable employee accounts", async ({
  page,
  request,
  browser,
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
  const base = `/api/v1/orgs/${orgId}/accounts`
  const employees = await request.get(base, { headers })
  const targets = (await employees.json()).data.filter(
    (employee: { employee_code: string }) =>
      ["E08", "E09"].includes(employee.employee_code),
  )
  expect(targets).toHaveLength(2)
  for (const employee of targets) {
    if (!employee.account) {
      const issued = await request.post(`${base}/issue`, {
        headers,
        data: { employee_ids: [employee.id] },
      })
      expect(issued.ok()).toBeTruthy()
    } else {
      const enabled = await request.post(
        `${base}/${employee.account.user_id}/enable`,
        { headers },
      )
      expect(enabled.ok()).toBeTruthy()
    }
  }
  await page.goto("/admin/accounts")
  const row = (code: string) =>
    page
      .getByRole("row")
      .filter({ has: page.getByRole("cell", { name: code, exact: true }) })
  await row("E08").getByRole("button", { name: "重置密码" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("重置后原密码立即失效")
  await expectChineseCopy(page)
  await dialog.getByRole("button", { name: "确认重置密码" }).click()
  const password = dialog.getByRole("textbox", { name: "新密码" })
  await expect(password).not.toHaveValue("")
  const newPassword = await password.inputValue()
  await expectChineseCopy(page, [newPassword])
  await page.screenshot({
    path: "test-results/admin-reset-password.png",
    fullPage: true,
  })
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    })
    document.execCommand = () => false
  })
  await dialog.getByRole("button", { name: "复制", exact: true }).click()
  await expect(dialog.getByRole("status")).toHaveText("请手动复制")
  expect(
    await password.evaluate(
      (element: HTMLInputElement) =>
        element.selectionEnd! - element.selectionStart!,
    ),
  ).toBe(newPassword.length)
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await expect(page.locator(`input[value="${newPassword}"]`)).toHaveCount(0)
  await row("E08").getByRole("button", { name: "重置密码" }).click()
  await expect(dialog.getByRole("textbox", { name: "新密码" })).toHaveCount(0)
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  const context = await browser.newContext({
    storageState: { cookies: [], origins: [] },
  })
  const employeePage = await context.newPage()
  async function login(username: string, secret: string) {
    await employeePage.goto("/login")
    await employeePage.getByTestId("email-input").fill(username)
    await employeePage.getByTestId("password-input").fill(secret)
    await employeePage
      .getByRole("button", { name: "登录", exact: true })
      .click()
  }
  try {
    await login("E08", newPassword)
    await expect(employeePage).toHaveURL(/\/change-password$/)
    await context.clearCookies()
    await employeePage.evaluate(() => localStorage.clear())
    const fresh = await request.get(base, { headers })
    const employee = (await fresh.json()).data.find(
      (entry: { employee_code: string }) => entry.employee_code === "E09",
    )
    const reset = await request.post(
      `${base}/${employee.account.user_id}/reset-password`,
      { headers },
    )
    expect(reset.ok()).toBeTruthy()
    const secret = (await reset.json()).initial_password
    await row("E09").getByRole("button", { name: "停用", exact: true }).click()
    await expect(dialog).toContainText("停用后这名员工不能登录，画像和证据保留")
    await expectChineseCopy(page)
    await page.screenshot({
      path: "test-results/admin-disable-account.png",
      fullPage: true,
    })
    await dialog.getByRole("button", { name: "确认停用" }).click()
    await expect(row("E09")).toContainText("已停用")
    await login("E09", secret)
    await expect(
      employeePage.getByText("账号已停用，请联系企业管理员", { exact: true }),
    ).toBeVisible()
    await row("E09").getByRole("button", { name: "启用", exact: true }).click()
    await expectChineseCopy(page)
    await dialog.getByRole("button", { name: "确认启用" }).click()
    await expect(row("E09")).toContainText("待首次登录")
    await login("E09", secret)
    await expect(employeePage).toHaveURL(/\/change-password$/)
  } finally {
    await context.close()
  }
})
