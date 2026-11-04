import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("employee previews imports, downloads files, submits and withdraws", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.algorithm@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL(/\/(inbox|employees\/[^/]+)$/)
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  await page
    .getByRole("button", { name: "导入 AI 生成的画像", exact: true })
    .click()
  const dialog = page.getByRole("dialog")
  for (const label of [
    "下载格式说明",
    "下载生成指南（skill）",
    "下载示例",
    "下载本企业能力词典",
  ]) {
    const downloading = page.waitForEvent("download")
    await dialog.getByRole("button", { name: label, exact: true }).click()
    expect(await (await downloading).failure()).toBeNull()
  }
  const file = dialog.getByLabel("选择画像 JSON 文件")
  await file.setInputFiles({
    name: "bad.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"items":[{}]}'),
  })
  await dialog.getByRole("button", { name: "预览导入", exact: true }).click()
  await expect(dialog.getByRole("alert")).toContainText("第 1 项 basis")
  await expect(
    dialog.getByRole("button", { name: "确认提交", exact: true }),
  ).toHaveCount(0)
  const body = {
    schema_version: "1",
    employee_email: "employee.algorithm@demo.example.com",
    capability_schema_version: 1,
    items: [
      {
        capability_key: "rknn_deployment",
        statement: "合成导入：完成模型部署",
        basis: "合成项目中完成开发板部署",
        uncertainty: "长期稳定性待验证",
      },
    ],
  }
  await file.setInputFiles({
    name: "synthetic-profile.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(body)),
  })
  const previewing = page.waitForResponse((r) =>
    r.url().includes("/me/profile-import?confirm=false"),
  )
  await dialog.getByRole("button", { name: "预览导入", exact: true }).click()
  const preview = await (await previewing).json()
  await expect(dialog.getByRole("region", { name: "导入预览" })).toContainText(
    "长期稳定性待验证",
  )
  await page.screenshot({
    path: "test-results/profile-import.png",
    fullPage: true,
  })
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const memberships = await page.request.get("/api/v1/me/memberships", {
    headers: { Authorization: `Bearer ${token}` },
  })
  const org = (await memberships.json())[0].org.id
  const requests = await page.request.get(`/api/v1/orgs/${org}/me/requests`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(
    (await requests.json()).data.some(
      (r: { id: string }) => r.id === preview.request_id,
    ),
  ).toBe(false)
  const submitting = page.waitForResponse((r) =>
    r.url().includes("/me/profile-import?confirm=true"),
  )
  await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
  const submitted = await (await submitting).json()
  await expect(dialog).toHaveCount(0)
  const pending = page.getByRole("region", { name: "其他待处理请求" })
  await expect(pending).toContainText("待经理审批 · 1 项改动")
  const saved = await page.request.get(`/api/v1/orgs/${org}/me/requests`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const imported = (await saved.json()).data.find(
    (r: { id: string }) => r.id === submitted.request_id,
  )
  expect(imported.submitted_via).toBe("import")
  expect(imported.client_name).toBe("synthetic-profile.json")
  page.once("dialog", (alert) => alert.accept())
  await pending.getByRole("button", { name: "撤回", exact: true }).click()
  await expect(pending).toHaveCount(0)
})

test("editing an initial candidate sends uncertainty back unchanged", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL(/\/(inbox|employees\/[^/]+)$/)
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  const initial = page.getByRole("region", {
    name: "初始画像草稿",
    exact: true,
  })
  await initial
    .getByRole("button", { name: "修改", exact: true })
    .first()
    .click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "陈述", exact: true })
    .first()
    .fill("合成修改，不丢不确定说明")
  let uncertainties: (string | null)[] = []
  await page.route("**/me/requests/*/items", async (route) => {
    uncertainties = route
      .request()
      .postDataJSON()
      .items.map((item: { uncertainty: string | null }) => item.uncertainty)
    // Capture the UI boundary without modifying the shared demo candidate.
    await route.fulfill({ json: { id: "synthetic", revision: 2 } })
  })
  await dialog.getByRole("button", { name: "保存草稿", exact: true }).click()
  await expect(dialog.getByRole("status")).toHaveText("草稿已保存")
  expect(uncertainties).toHaveLength(2)
  expect(
    uncertainties.every((value) => value?.includes("任务关联不等于已独立完成")),
  ).toBe(true)
})
