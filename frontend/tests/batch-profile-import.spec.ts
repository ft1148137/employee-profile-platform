import { expect, test } from "@playwright/test"
import type { AccountsPublic, ChangeRequestsPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin imports three candidates, skips existing requests, employee edits and submits", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const suffix = crypto.randomUUID()
  const password = "synthetic-password-2026"
  const adminEmail = `batch-admin-${suffix}@example.com`
  const emails = [1, 2, 3].map((n) => `batch-${n}-${suffix}@example.com`)
  const setupToken = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(setupToken.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setupToken.json()).token,
      org_name: "合成批量导入企业",
      admin_username: adminEmail,
      admin_password: password,
      admin_full_name: "测试管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: token } = await initialized.json()
  const headers = { Authorization: `Bearer ${token}` }
  const base = `/api/v1/orgs/${orgId}`
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "synthetic.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\n${emails.map((email, i) => `B${i + 1},测试员工${i + 1},研发部,工程师,,${email}`).join("\n")}`,
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
    (
      await request.post(`${base}/capability-schema`, {
        headers,
        data: { template_id: "tech-rnd" },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const person = (employee_email: string) => ({
    schema_version: "1",
    employee_email,
    capability_schema_version: 1,
    items: [
      {
        capability_key: "rknn_deployment",
        statement: "合成导入：完成模型部署",
        basis: "在合成项目部署开发板",
        uncertainty: "长期稳定性待验证",
      },
    ],
  })
  await logInUser(page, adminEmail, password)
  await page.getByRole("link", { name: "批量导入画像", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "批量导入画像", exact: true }),
  ).toBeVisible()
  await expectChineseCopy(page)
  for (const name of ["下载格式说明", "下载生成指南", "下载示例"]) {
    const downloading = page.waitForEvent("download")
    await page.getByRole("button", { name, exact: true }).click()
    expect(await (await downloading).failure()).toBeNull()
  }
  const input = page.getByLabel("选择画像文件（可多选）")
  await input.setInputFiles({
    name: "bad.json",
    mimeType: "application/json",
    buffer: Buffer.from('[{"items":[{}]}]'),
  })
  await page.getByRole("button", { name: "预览导入", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText(
    "bad.json 第 1 个人 第 1 项 basis",
  )
  await expect(
    page.getByRole("button", { name: "确认导入", exact: true }),
  ).toHaveCount(0)
  await input.setInputFiles([
    {
      name: "team.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(emails.slice(0, 2).map(person))),
    },
    {
      name: "single.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(person(emails[2]))),
    },
  ])
  await page.getByRole("button", { name: "预览导入", exact: true }).click()
  const planned = page.getByRole("region", { name: "导入预览", exact: true })
  await expect(
    planned.getByRole("cell", { name: "将创建", exact: true }),
  ).toHaveCount(3)
  await expect(planned).not.toContainText("合成导入：完成模型部署")
  await page.screenshot({
    path: "test-results/batch-import-preview.png",
    fullPage: true,
  })
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(
    page.getByRole("cell", { name: "已创建", exact: true }),
  ).toHaveCount(3)
  await input.setInputFiles({
    name: "again.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(person(emails[0]))),
  })
  await page.getByRole("button", { name: "预览导入", exact: true }).click()
  await expect(
    page.getByRole("cell").filter({ hasText: "已有草稿或待本人过目的请求" }),
  ).toHaveCount(1)
  await expect(
    page.getByRole("button", { name: "确认导入", exact: true }),
  ).toBeDisabled()
  const accounts: AccountsPublic = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  const account = accounts.data.find(
    (row) => row.account?.username === emails[0],
  )?.account
  expect(account).toBeTruthy()
  const reset = await request.post(
    `${base}/accounts/${account!.user_id}/reset-password`,
    { headers },
  )
  expect(reset.ok()).toBeTruthy()
  const initialPassword = (await reset.json()).initial_password
  await logOutUser(page)
  await page.getByTestId("email-input").fill(emails[0])
  await page.getByTestId("password-input").fill(initialPassword)
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await page.getByLabel("当前密码", { exact: true }).fill(initialPassword)
  await page.getByLabel("新密码", { exact: true }).fill(password)
  await page.getByLabel("确认新密码", { exact: true }).fill(password)
  await page.getByRole("button", { name: "保存密码", exact: true }).click()
  await expect(page).toHaveURL("/inbox")
  await expect(
    page.getByText("管理员导入了 AI 生成的画像，请过目后提交", { exact: true }),
  ).toBeVisible()
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  const candidate = page.getByRole("region", {
    name: "导入画像草稿",
    exact: true,
  })
  await expect(candidate).toContainText("待你过目：管理员导入的画像")
  await candidate.getByRole("button", { name: "修改", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "陈述", exact: true })
    .fill("本人核对：完成模型部署和验证")
  await dialog.getByRole("button", { name: "预览并提交", exact: true }).click()
  await expect(dialog).toContainText("长期稳定性待验证")
  await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByRole("region", { name: "其他待处理请求" }),
  ).toContainText("待经理审批")
  const employeeToken = await page.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  const submitted: ChangeRequestsPublic = await (
    await page.request.get(`${base}/me/requests`, {
      headers: { Authorization: `Bearer ${employeeToken}` },
    })
  ).json()
  expect(submitted.data[0].origin).toBe("import")
  expect(submitted.data[0].status).toBe("pending_review")
  expect(submitted.data[0].items?.[0].uncertainty).toBe("长期稳定性待验证")
  expect(submitted.data[0].items?.[0].after?.statement).toBe(
    "本人核对：完成模型部署和验证",
  )
})
