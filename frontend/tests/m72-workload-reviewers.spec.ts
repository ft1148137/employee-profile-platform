import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("员工提交负荷申请前后看到审批人", async ({ page, request }) => {
  test.setTimeout(120000)
  const password = "synthetic-capacity-password-2026"
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
    org_name: "合成负荷审批企业",
    admin_username: `capacity-review-${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nM,合成审批经理,研发,,m-${crypto.randomUUID()}@example.com\nE,合成申请员工,研发,M,e-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await post(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    admin.access_token,
  )
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const manager = accounts.find((row) => row.employee_code === "M")!
  const employee = accounts.find((row) => row.employee_code === "E")!
  await post(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    admin.access_token,
  )
  const enabled = await request.put(`${base}/capacity-settings`, {
    headers,
    data: { enabled: true, requester_policy: "everyone" },
  })
  expect(enabled.ok(), await enabled.text()).toBeTruthy()
  const reset = await post(
    `${base}/accounts/${employee.account!.user_id}/reset-password`,
    {},
    admin.access_token,
  )
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok(), await login.text()).toBeTruthy()
  const changed = await request.patch("/api/v1/users/me/password", {
    headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
    data: {
      current_password: reset.initial_password,
      new_password: password,
    },
  })
  expect(changed.ok(), await changed.text()).toBeTruthy()
  const token = (await changed.json()).access_token
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token, orgId: admin.org_id },
  )
  await page.goto("/capacity")
  await page.getByRole("button", { name: "申请调整", exact: true }).click()
  const drawer = page.getByRole("dialog", { name: "申请调整" })
  const form = drawer.getByRole("form", { name: "申请调整" })
  await expect(form).toContainText("将交给 合成审批经理 审批")
  await form.getByLabel("申请负荷（%）").fill("50")
  await form.getByLabel("申请原因").fill("合成调整原因")
  await form.getByRole("button", { name: "提交申请" }).click()
  await expect(drawer).toBeHidden()
  await expect(
    page.getByRole("region", { name: "调整申请记录" }),
  ).toContainText("已交给 合成审批经理 审批")
})
