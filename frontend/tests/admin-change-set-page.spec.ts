import type { APIRequestContext } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { dictionaryDefinitions, expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("page shows assistant reminder draft and can apply or discard it", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createAdmin(
    request,
    modelUrl,
  )
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=reminders")
  const section = page.getByRole("region", { name: "持续更新提醒" })
  async function propose() {
    await page.getByRole("button", { name: "打开管理助手" }).click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await dialog.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      dialog.getByRole("region", { name: "设置改动确认卡" }).last(),
    ).toBeVisible({ timeout: 30000 })
    await page.keyboard.press("Escape")
    await expect(section.getByRole("combobox")).toHaveText("每年")
    await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("36")
    await expect(
      section.locator('[data-assistant-suggested="true"]'),
    ).toHaveCount(2)
    await expect(section.getByText("助手建议", { exact: true })).toHaveCount(2)
    await expect(
      section.getByRole("button", { name: "保存提醒设置" }),
    ).toBeDisabled()
    await page.screenshot({
      path: testInfo.outputPath("draft-form.png"),
      fullPage: true,
    })
  }
  await propose()
  expect(
    (await (await request.get(`${base}/update-settings`, { headers })).json())
      .reminder_schedule,
  ).toBe("quarterly")
  await section.getByRole("button", { name: "确认应用" }).click()
  await expect(
    section.getByRole("region", { name: "页面草稿确认" }),
  ).toHaveCount(0)
  await expect(section.getByRole("combobox")).toHaveText("每年")
  expect(
    (await (await request.get(`${base}/update-settings`, { headers })).json())
      .reminder_schedule,
  ).toBe("yearly")
  // Restore through the existing manual API so the next proposal changes both fields again.
  expect(
    (
      await request.put(`${base}/update-settings`, {
        headers,
        data: { reminder_schedule: "quarterly", stale_after_months: 24 },
      })
    ).ok(),
  ).toBeTruthy()
  await page.reload()
  await propose()
  await page.reload()
  await expect(section.getByRole("combobox")).toHaveText("每年")
  await section.getByRole("button", { name: "放弃", exact: true }).click()
  await expect(section.getByRole("combobox")).toHaveText("每季度")
  await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("24")
  await expect(
    section.locator('[data-assistant-suggested="true"]'),
  ).toHaveCount(0)
  await expectChineseCopy(page)
  // A new conversation must stop showing drafts from the previous conversation.
  await propose()
  await page.getByRole("button", { name: "打开管理助手" }).click()
  await page.getByRole("button", { name: "新对话", exact: true }).click()
  await page.keyboard.press("Escape")
  await expect(section.getByRole("combobox")).toHaveText("每季度")
  await expect(
    section.getByRole("region", { name: "页面草稿确认" }),
  ).toHaveCount(0)
})

test("approval page highlights assistant draft and requires both confirmations", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createAdmin(
    request,
    modelUrl,
  )
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=approval")
  await page.getByRole("button", { name: "打开管理助手" }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("关闭经理确认")
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    dialog.getByRole("region", { name: "设置改动确认卡" }),
  ).toBeVisible({ timeout: 30000 })
  await page.keyboard.press("Escape")
  const form = page.getByRole("form", { name: "审批规则", exact: true })
  await expect(
    form.getByRole("checkbox", { name: "全企业需要经理确认" }),
  ).not.toBeChecked()
  await expect(form.locator('[data-assistant-suggested="true"]')).toHaveCount(1)
  await expect(form.getByText("助手建议", { exact: true })).toBeVisible()
  await expect(
    form.getByRole("button", { name: "保存审批规则" }),
  ).toBeDisabled()
  const card = page.getByRole("region", { name: "页面草稿确认" })
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(true)
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card).toHaveCount(0)
  await expect(
    form.getByRole("checkbox", { name: "全企业需要经理确认" }),
  ).not.toBeChecked()
  await expect(form.locator('[data-assistant-suggested="true"]')).toHaveCount(0)
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(false)
  await expectChineseCopy(page)
})

test("drafting page highlights a department draft and applies it after confirmation", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createAdmin(
    request,
    modelUrl,
  )
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,synthetic@example.com\n",
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const preview = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  const departments = await (
    await request.get(`${base}/departments`, { headers })
  ).json()
  const departmentId = departments.data[0].id
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=drafting")
  const department = page.getByRole("checkbox", {
    name: /^研发部\s*含下级部门$/,
  })
  await expect(department).not.toBeChecked()
  await page.getByRole("button", { name: "打开管理助手" }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill(`关闭部门自动起草 ${departmentId}`)
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    dialog.getByRole("region", { name: "设置改动确认卡" }),
  ).toBeVisible({ timeout: 30000 })
  await page.keyboard.press("Escape")
  await expect(department).toBeChecked()
  const suggested = page.locator('[data-assistant-suggested="true"]')
  await expect(suggested).toHaveCount(1)
  await expect(suggested.getByText("助手建议", { exact: true })).toBeVisible()
  await expect(
    suggested.getByRole("checkbox", { name: /^研发部/ }),
  ).toBeChecked()
  await expect(
    page.getByRole("button", { name: "保存", exact: true }),
  ).toBeDisabled()
  const read = async () =>
    (await (await request.get(`${base}/drafting-settings`, { headers })).json())
      .disabled_department_ids
  expect(await read()).toEqual([])
  const card = page.getByRole("region", { name: "页面草稿确认" })
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card).toHaveCount(0)
  await expect(suggested).toHaveCount(0)
  await expect(department).toBeChecked()
  expect(await read()).toEqual([departmentId])
})

test("dictionary page highlights a modified capability and publishes a new version", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createAdmin(
    request,
    modelUrl,
  )
  const created = await request.put(`${base}/capabilities`, {
    headers,
    data: { definitions: dictionaryDefinitions, expected_version: 0 },
  })
  expect(created.ok()).toBeTruthy()
  const original = await created.json()
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("button", { name: "打开管理助手" }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("修改能力说明")
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    dialog.getByRole("region", { name: "设置改动确认卡" }),
  ).toBeVisible({ timeout: 30000 })
  await page.keyboard.press("Escape")
  const editor = page.getByRole("region", { name: "网页编辑能力词典" })
  const suggested = editor.locator('[data-assistant-suggested="true"]')
  await expect(suggested).toHaveCount(1)
  await expect(
    suggested.getByText("助手建议 · 修改", { exact: true }),
  ).toBeVisible()
  await expect(
    suggested.getByRole("article", { name: "合成能力" }).getByLabel("能力说明"),
  ).toHaveValue("助手修改后的能力说明")
  await expect(editor.getByRole("button", { name: "预览改动" })).toBeDisabled()
  const read = async () =>
    (await request.get(`${base}/capabilities`, { headers })).json()
  const before = await read()
  expect(before.schema_version).toMatchObject(original)
  expect(before.data[0].description).toBe("合成能力说明")
  const card = page.getByRole("region", { name: "页面草稿确认" })
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card).toHaveCount(0)
  await expect(page.getByText("当前第 2 版 · 1 项能力")).toBeVisible()
  await expect(suggested).toHaveCount(0)
  const after = await read()
  expect(after.schema_version.version).toBe(original.version + 1)
  expect(after.schema_version.id).not.toBe(original.id)
  expect(after.data[0].description).toBe("助手修改后的能力说明")
  const history = await request.get(`${base}/audit-events`, {
    headers,
    params: { target_type: "capability_schema_version" },
  })
  expect(history.ok()).toBeTruthy()
  // Labels resolve stored schema rows, so a deleted old version cannot pass this check.
  expect((await history.json()).data).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        target_id: original.id,
        target_label: `第 ${original.version} 版`,
      }),
      expect.objectContaining({
        target_id: after.schema_version.id,
        target_label: `第 ${after.schema_version.version} 版`,
      }),
    ]),
  )
})

async function createAdmin(request: APIRequestContext, modelUrl: string) {
  const email = `admin-page-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "提醒设置合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
  expect(
    (
      await request.put(`${base}/model-setting`, {
        headers,
        data: {
          enabled: true,
          kind: "openai_compatible",
          provider: "openai-compatible",
          model: "synthetic",
          base_url: modelUrl,
          api_key: "synthetic-key",
        },
      })
    ).ok(),
  ).toBeTruthy()
  return { email, password, base, headers }
}

test("model draft highlights fields and applies only after two confirmations", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createAdmin(
    request,
    modelUrl,
  )
  await logInUser(page, email, password)
  await page.goto("/admin/data-ai?tab=model")
  await page.getByRole("button", { name: "打开管理助手" }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("换模型")
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    dialog.getByRole("region", { name: "设置改动确认卡" }),
  ).toBeVisible({ timeout: 30000 })
  await page.keyboard.press("Escape")
  const field = page.getByLabel("模型名", { exact: true })
  await expect(field).toHaveValue("synthetic-updated")
  const suggested = page.locator('[data-assistant-suggested="true"]')
  await expect(suggested.getByLabel("模型名", { exact: true })).toHaveValue(
    "synthetic-updated",
  )
  await expect(
    page.getByRole("button", { name: "保存", exact: true }),
  ).toBeDisabled()
  const read = async () =>
    (await (await request.get(`${base}/model-setting`, { headers })).json())
      .model
  expect(await read()).toBe("synthetic")
  const card = page.getByRole("region", { name: "页面草稿确认" })
  await expect(card.getByText(/数据将发送给/)).toBeVisible()
  await expect(card.getByText(/密钥不回滚/)).toBeVisible()
  await card.getByRole("button", { name: "测试连接", exact: true }).click()
  await expect(card.getByText("测试连接：成功", { exact: true })).toBeVisible({
    timeout: 30000,
  })
  expect(await read()).toBe("synthetic")
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  expect(await read()).toBe("synthetic")
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card).toHaveCount(0)
  expect(await read()).toBe("synthetic-updated")
  await expect(field).toHaveValue("synthetic-updated")
  await expect(suggested).toHaveCount(0)
})
