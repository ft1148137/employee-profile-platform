import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("administrator renames, moves and deletes an empty department", async ({
  page,
  request,
}) => {
  const email = `department-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成部门编辑企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
  const preview = await request.post(`${base}/roster/single-change`, {
    headers,
    data: {
      kind: "onboard",
      employee_code: "S16",
      display_name: "合成员工",
      email: "s16@example.com",
      department: "空部门/目标部门",
    },
  })
  expect(preview.ok()).toBeTruthy()
  const record = await preview.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${record.id}/apply`, {
        headers,
        data: { base_revision: record.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/org-chart")
  const navigation = page.getByRole("navigation", { name: "部门导航" })
  const details = page.getByRole("complementary", { name: "部门详情" })
  await navigation.getByRole("button", { name: /^目标部门/ }).click()
  await page.getByRole("button", { name: "管理部门", exact: true }).click()
  await expect(
    details.getByRole("button", { name: "改名目标部门" }),
  ).toBeFocused()
  await details
    .getByRole("button", { name: "移动到目标部门", exact: true })
    .click()
  const dialog = page.getByRole("dialog")
  await dialog.getByLabel("上级部门").selectOption("")
  await dialog.getByRole("button", { name: "确认保存" }).click()
  await expect(dialog).toHaveCount(0)
  await navigation.getByRole("button", { name: /^空部门/ }).click()
  await details.getByRole("button", { name: "改名空部门", exact: true }).click()
  await dialog.getByLabel("部门名称").fill("新部门")
  await dialog.getByRole("button", { name: "确认保存" }).click()
  await expect(
    navigation.getByRole("button", { name: /^新部门/ }),
  ).toBeVisible()
  const departments = (
    await (await request.get(`${base}/departments`, { headers })).json()
  ).data
  const target = departments.find(
    (d: { name: string }) => d.name === "目标部门",
  )
  const edited = departments.find((d: { name: string }) => d.name === "新部门")
  await details
    .getByRole("button", { name: "移动到新部门", exact: true })
    .click()
  await expect(dialog).toContainText("查看和审批范围")
  await dialog.getByLabel("上级部门").selectOption(target.id)
  await dialog.getByRole("button", { name: "确认保存" }).click()
  await expect(dialog).toHaveCount(0)
  expect(
    (
      await (await request.get(`${base}/departments`, { headers })).json()
    ).data.find((d: { id: string }) => d.id === edited.id).parent_department_id,
  ).toBe(target.id)
  await details.getByRole("button", { name: "删除新部门", exact: true }).click()
  await expect(dialog).toContainText("不可撤销")
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  await expect(
    navigation.getByRole("button", { name: /^新部门/ }),
  ).toBeVisible()
  await details.getByRole("button", { name: "删除新部门", exact: true }).click()
  await dialog.getByRole("button", { name: "确认删除" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(navigation.getByRole("button", { name: /^新部门/ })).toHaveCount(
    0,
  )
})

test("department manager cannot see department editing buttons", async ({
  page,
}) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/org-chart")
  await expect(
    page
      .getByRole("navigation", { name: "部门导航" })
      .getByRole("button", { name: /^平台研发部/ }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: /^(管理部门|改名|移动到|删除)/ }),
  ).toHaveCount(0)
})
