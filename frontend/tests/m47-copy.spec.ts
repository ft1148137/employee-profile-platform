import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { importRoster } from "./utils/roster"

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page, request }) => {
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(token.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成说明文字测试企业",
      admin_username: `copy-${crypto.randomUUID()}@example.com`,
      admin_password: "synthetic-copy-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  await page.goto("/login")
  await page.evaluate(
    ({ access_token, org_id }) => {
      localStorage.setItem("access_token", access_token)
      localStorage.setItem("current_org_id", org_id)
    },
    { access_token, org_id },
  )
})

test("谁能看到说明数据存放和咨询方式", async ({ page, request }) => {
  await importRoster(
    page,
    Buffer.from(
      "工号,姓名,部门,职位,上级工号,邮箱\nCOPY1,合成员工,合成部门,员工,,copy-employee@example.com\n",
    ),
  )
  const { orgId, token } = await page.evaluate(() => ({
    orgId: localStorage.getItem("current_org_id"),
    token: localStorage.getItem("access_token"),
  }))
  const accounts = await request.get(`/api/v1/orgs/${orgId}/accounts`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(accounts.ok()).toBeTruthy()
  const employees: EmployeeAccount[] = (await accounts.json()).data
  const employee = employees.find((entry) => entry.employee_code === "COPY1")!
  await page.goto(`/employees/${employee.id}`)
  await page.getByText("谁能看到", { exact: true }).click()
  await expect(
    page.getByText(
      "数据保存在本企业自己部署的服务器上；存放位置、备份和保留期限请问企业管理员。",
      { exact: true },
    ),
  ).toBeVisible()
})

test("未匹配 Jira 账号说明邮箱可能隐藏及核对办法", async ({ page }) => {
  await page.goto("/admin/identities")
  const dialog = page.getByRole("dialog", { name: "未匹配 Jira 账号" })
  await expect(
    dialog.getByText(
      "Jira 可能隐藏邮箱，显示名不一定是真名，请和 Jira 管理员核对账号。",
      { exact: true },
    ),
  ).toBeVisible()
  await expect(dialog.getByRole("tab", { name: "未关联（0）" })).toBeVisible()
})
