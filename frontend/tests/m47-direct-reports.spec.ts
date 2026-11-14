import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("值班经理在设置页看到实际审批人，授权为审批人后不再提示", async ({
  page,
  request,
}) => {
  const password = "synthetic-direct-reports-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成直属下属企业",
      admin_username: `direct-${crypto.randomUUID()}@example.com`,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\nMANAGER,合成部门经理,门店,部门经理,,manager-${crypto.randomUUID()}@example.com\nDUTY,合成值班经理,门店,值班经理,MANAGER,duty-${crypto.randomUUID()}@example.com\nREPORT,合成直属下属,门店,员工,DUTY,report-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const manager = accounts.find(
    (account) => account.employee_code === "MANAGER",
  )!
  const duty = accounts.find((account) => account.employee_code === "DUTY")!
  expect(
    (
      await request.post(`${base}/role-grants`, {
        headers,
        data: {
          user_id: manager.account!.user_id,
          role: "dept_manager",
          department_id: manager.department!.id,
        },
      })
    ).ok(),
  ).toBeTruthy()
  const reset = await (
    await request.post(
      `${base}/accounts/${duty.account!.user_id}/reset-password`,
      { headers },
    )
  ).json()
  const login = await (
    await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
  ).json()
  const changed = await (
    await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${login.access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
  ).json()
  await page.goto("/login")
  await page.evaluate(
    ({ accessToken, orgId }) => {
      localStorage.setItem("access_token", accessToken)
      localStorage.setItem("current_org_id", orgId)
    },
    { accessToken: changed.access_token, orgId: admin.org_id },
  )
  await page.goto("/settings")
  const panel = page.getByRole("tabpanel")
  await expect(panel).toContainText("合成直属下属（工号：REPORT）")
  await expect(panel).toContainText("由 合成部门经理（部门经理）审批")
  await expect(panel).toContainText(
    "你没有团队查看和审批权限，需要的话请找企业管理员",
  )
  expect(
    (
      await request.post(`${base}/role-grants`, {
        headers,
        data: {
          user_id: duty.account!.user_id,
          role: "dept_manager",
          department_id: duty.department!.id,
        },
      })
    ).ok(),
  ).toBeTruthy()
  const refreshed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/me/direct-reports") && response.ok(),
  )
  await page.reload()
  await refreshed
  await expect(panel.getByText("姓名", { exact: true })).toBeVisible()
  await expect(panel).not.toContainText("你的直属下属")
})
