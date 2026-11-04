import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("drafting settings and personal choice persist with role boundaries", async ({
  page,
  browser,
  request,
}) => {
  const password = process.env.DEMO_USER_PASSWORD!
  await logInUser(page, "admin@demo.example.com", password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const orgId = memberships[0].org.id
  const base = `/api/v1/orgs/${orgId}`
  const original = await (
    await request.get(`${base}/drafting-settings`, { headers })
  ).json()
  const capabilities = await (
    await request.get(`${base}/capabilities`, { headers })
  ).json()
  const capability = capabilities.data[0]
  const employeePage = await browser.newPage({
    baseURL: new URL(page.url()).origin,
    storageState: { cookies: [], origins: [] },
  })
  const managerPage = await browser.newPage({
    baseURL: new URL(page.url()).origin,
    storageState: { cookies: [], origins: [] },
  })
  await logInUser(employeePage, "employee.platform@demo.example.com", password)
  await expect(employeePage).toHaveURL("/inbox")
  await employeePage
    .getByRole("link", { name: "我的画像", exact: true })
    .click()
  const employeeToken = await employeePage.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  const employeeHeaders = { Authorization: `Bearer ${employeeToken}` }
  const own = await (
    await request.get(`${base}/me/auto-draft`, { headers: employeeHeaders })
  ).json()
  try {
    await page.getByRole("link", { name: "自动起草设置", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "自动起草设置" }),
    ).toBeVisible()
    await expectChineseCopy(page)
    const capabilityBox = page.getByRole("checkbox", {
      name: capability.name,
      exact: true,
    })
    const departmentBox = page.getByRole("checkbox", {
      name: /^平台研发部\s*含下级部门$/,
      exact: true,
    })
    await capabilityBox.setChecked(
      !original.excluded_capability_keys.includes(capability.key),
    )
    await departmentBox.check()
    await page.route("**/drafting-settings", async (route) => {
      if (route.request().method() === "PUT")
        await route.fulfill({
          status: 422,
          json: { detail: { message: "不认识的能力 key 或部门：测试能力" } },
        })
      else await route.continue()
    })
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("alert")).toContainText("测试能力")
    await page.unroute("**/drafting-settings")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toContainText("已保存")
    await page.reload()
    await expect(capabilityBox).toBeChecked({
      checked: !original.excluded_capability_keys.includes(capability.key),
    })
    await expect(departmentBox).toBeChecked()
    await page.screenshot({
      path: "test-results/drafting-settings.png",
      fullPage: true,
    })
    await employeePage.reload()
    await expect(
      employeePage.getByText("你所在的部门目前关闭了自动起草。"),
    ).toBeVisible()
    const toggle = employeePage.getByRole("switch", {
      name: "让系统根据工作记录自动起草我的画像",
    })
    if (own.enabled) await toggle.click()
    await expect(toggle).not.toBeChecked()
    await employeePage.reload()
    await expect(toggle).not.toBeChecked()
    await expect(
      employeePage.getByRole("link", { name: "自动起草设置", exact: true }),
    ).toHaveCount(0)
    await expectChineseCopy(employeePage)
    await employeePage.screenshot({
      path: "test-results/auto-draft-preference.png",
      fullPage: true,
    })
    await logInUser(managerPage, "manager.platform@demo.example.com", password)
    await managerPage.goto(new URL(employeePage.url()).pathname)
    await expect(
      managerPage.getByRole("heading", { name: "张伟", exact: true }),
    ).toBeVisible()
    await expect(managerPage.getByRole("switch")).toHaveCount(0)
    await expect(
      managerPage.getByRole("link", { name: "自动起草设置", exact: true }),
    ).toHaveCount(0)
  } finally {
    expect(
      (
        await request.put(`${base}/drafting-settings`, {
          headers,
          data: {
            excluded_capability_keys: original.excluded_capability_keys,
            disabled_department_ids: original.disabled_department_ids,
          },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.put(`${base}/me/auto-draft`, {
          headers: employeeHeaders,
          data: { enabled: own.enabled },
        })
      ).ok(),
    ).toBeTruthy()
    await employeePage.close()
    await managerPage.close()
  }
})
