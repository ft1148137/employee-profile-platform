import { expect, test } from "@playwright/test"
import { importRoster } from "./utils/roster"

test.use({ storageState: { cookies: [], origins: [] } })

test("opt-in work attributes import and personnel tenure", async ({
  page,
  request,
}) => {
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(token.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成员工档案企业",
      admin_username: `fields-${crypto.randomUUID()}@example.com`,
      admin_full_name: "档案管理员",
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token: access_token, orgId: org_id },
  )
  await page.goto("/admin/assistant?prompt=帮我按这份名单导入员工")
  await expect(
    page.getByRole("textbox", { name: "发给管理助手的消息" }),
  ).toHaveValue("帮我按这份名单导入员工")
  await page.goto("/admin/people?tab=roster")
  await expect(page).toHaveURL(/\/admin\/assistant(?:\?session=[0-9a-f-]+)?$/)
  await page.goto("/admin/people?tab=accounts")
  await page.getByRole("button", { name: "档案字段", exact: true }).click()
  const switches = page.getByRole("region", { name: "员工档案字段" })
  for (const label of ["岗位序列", "工作地点", "入职日期"]) {
    const toggle = switches.getByRole("switch", { name: label, exact: true })
    await expect(toggle).not.toBeChecked()
    await expect(toggle).toBeEnabled()
    await toggle.click()
    await expect(toggle).toBeChecked()
    await expect(toggle).toBeEnabled()
  }
  const hireYear = new Date().getFullYear() - 3
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .click()
  await importRoster(
    page,
    Buffer.from(
      `工号,姓名,部门,职位,上级工号,邮箱,岗位序列,工作地点,入职日期\nE1,合成档案员工,研发,工程师,,fields@example.com,技术,上海,${hireYear}-01-01\nE2,合成新员工,研发,工程师,,recent@example.com,技术,上海,${hireYear + 3}-01-01`,
    ),
  )
  const accounts = await request.get(`/api/v1/orgs/${org_id}/accounts`, {
    headers: { Authorization: `Bearer ${access_token}` },
  })
  expect(accounts.ok()).toBeTruthy()
  const people = (await accounts.json()).data
  await page.goto(`/admin/people/${people[0].id}?tab=fields`)
  const info = page.getByRole("region", { name: "员工信息" })
  await expect(info.getByText("技术", { exact: true })).toBeVisible()
  await expect(info.getByText("上海", { exact: true })).toBeVisible()
  await expect(info.getByText("入职 3 年", { exact: true })).toBeVisible()
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0)
  await expect(page.getByText("正在加载账号…", { exact: true })).toHaveCount(0)
  await expect(
    page.getByText("正在加载修改记录…", { exact: true }),
  ).toHaveCount(0)
  await page.screenshot({
    path: "../docs/screenshots/m4.2/employee-fields.png",
    fullPage: true,
  })
  await page.goto(`/admin/people/${people[1].id}?tab=fields`)
  await expect(page.getByText("不满 1 年", { exact: true })).toBeVisible()
})
