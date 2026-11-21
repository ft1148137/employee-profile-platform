import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("restore a previous manager through the existing transfer preview", async ({
  page,
  request,
}) => {
  const email = `rehire-${crypto.randomUUID()}@example.com`
  const password = "synthetic-rehire-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(token.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成返聘测试企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
  const managerEmail = `manager-${crypto.randomUUID()}@example.com`
  const employeeEmail = `employee-${crypto.randomUUID()}@example.com`
  const importRoster = async (manager: string) => {
    const response = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "合成返聘名单.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,上级工号,邮箱\nM,合成原上级,研发部,,${managerEmail}\nE,合成返聘员工,研发部,${manager},${employeeEmail}\n`,
          ),
        },
      },
    })
    expect(response.ok()).toBeTruthy()
    const preview = await response.json()
    expect(preview.can_apply).toBeTruthy()
    expect(
      (
        await request.post(`${base}/roster/imports/${preview.id}/apply`, {
          headers,
          data: { base_revision: preview.base_revision },
        })
      ).ok(),
    ).toBeTruthy()
  }
  await importRoster("M")
  const people: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const employee = people.find((e) => e.employee_code === "E")!
  const manager = people.find((e) => e.employee_code === "M")!
  const offboard = await request.post(`${base}/roster/single-change`, {
    headers,
    data: { kind: "offboard", employee_ids: [employee.id] },
  })
  expect(offboard.ok()).toBeTruthy()
  const preview = await offboard.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  await importRoster("")
  await logInUser(page, email, password)
  const noPreviousManager = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith(`/roster/employees/${manager.id}/previous-manager`) &&
      response.ok(),
  )
  await page.goto(`/employees/${manager.id}`)
  expect(await (await noPreviousManager).json()).toBeNull()
  await expect(
    page.getByText("直属上级：暂无上级", { exact: true }),
  ).toBeVisible()
  const reminder = page.getByRole("region", { name: "重新设置直属上级" })
  await expect(reminder).toBeHidden()

  await page.goto(`/employees/${employee.id}`)
  await expect(reminder).toContainText("直属上级未设置")
  await reminder.getByRole("button", { name: "恢复原上级：合成原上级" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("combobox")).toHaveValue(manager.id)
  await dialog.getByRole("button", { name: "预览变动" }).click()
  await expect(dialog.getByRole("region", { name: "名单预览" })).toContainText(
    "合成原上级",
  )
  await dialog.getByRole("button", { name: "确认调岗" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByText("直属上级：合成原上级", { exact: true }),
  ).toBeVisible()
  await expect(reminder).toHaveCount(0)

  const managerOffboard = await request.post(`${base}/roster/single-change`, {
    headers,
    data: { kind: "offboard", employee_ids: [manager.id] },
  })
  expect(managerOffboard.ok()).toBeTruthy()
  const managerPreview = await managerOffboard.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${managerPreview.id}/apply`, {
        headers,
        data: { base_revision: managerPreview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  await page.reload()
  await expect(reminder).toContainText(
    "原直属上级 合成原上级 已离职，请重新指定。",
  )
})
