import type { APIRequestContext } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
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
