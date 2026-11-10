import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("employee resumes either conversation after reload and in a new tab", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(60000)
  const email = `sessions-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  await logInUser(page, email, password)
  await page.setViewportSize({ width: 390, height: 900 })
  const open = async () =>
    page.getByRole("button", { name: "打开画像机器人" }).click()
  await open()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
  await expect(dialog.getByText("还没有历史对话")).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("empty-history-mobile.png"),
  })
  await expect(
    dialog.getByText("对话在 2 小时没有活动后会被清除", { exact: false }),
  ).toBeVisible()
  await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
  const send = async (text: string) => {
    await dialog.getByLabel("发给机器人的消息").fill(text)
    await dialog.getByRole("button", { name: "发送", exact: true }).click()
    await expect(dialog.getByLabel("发给机器人的消息")).toBeEnabled()
    await expect(dialog.getByRole("log")).toContainText(text)
  }
  await send("第一个合成对话")
  await dialog.getByRole("button", { name: "新对话", exact: true }).click()
  await expect(dialog.getByRole("log")).not.toContainText("第一个合成对话")
  await send("第二个合成对话：关闭经理确认")
  await page.reload()
  await open()
  await expect(dialog.getByRole("log")).toContainText("第二个合成对话")
  await expect(dialog.getByRole("log")).not.toContainText("第一个合成对话")
  const tab = await context.newPage()
  await tab.goto("/employees")
  await tab.getByRole("button", { name: "打开画像机器人" }).click()
  await expect(tab.getByRole("log")).toContainText("第二个合成对话")
  await tab.close()
  await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
  await dialog
    .getByRole("region", { name: "历史对话" })
    .getByRole("button", { name: /第一个合成对话/ })
    .click()
  await expect(dialog.getByRole("log")).toContainText("第一个合成对话")
  await expect(dialog.getByRole("log")).not.toContainText("第二个合成对话")
  await send("继续第一个对话")
  await expectChineseCopy(page)
  const previous = await page.evaluate(
    () =>
      Object.entries(localStorage).find(([key]) =>
        key.startsWith("bot-session:"),
      )?.[1],
  )
  await page.route(
    "**/bot/sessions/*/messages?*",
    (route) => route.fulfill({ status: 404, json: [] }),
    { times: 1 },
  )
  await page.reload()
  await open()
  await expect(dialog.getByLabel("发给机器人的消息")).toBeEnabled()
  await expect(dialog.getByRole("log")).not.toContainText("继续第一个对话")
  expect(
    await page.evaluate(
      () =>
        Object.entries(localStorage).find(([key]) =>
          key.startsWith("bot-session:"),
        )?.[1],
    ),
  ).not.toBe(previous)
  await send("清理后继续对话")
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage })),
  ).not.toContain("清理后继续对话")
})

test("admin resumes conversations across full screen and sidebar with matching page drafts", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const email = `admin-sessions-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "多对话合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const headers = { Authorization: `Bearer ${accessToken}` }
  expect(
    (
      await request.put(`/api/v1/orgs/${orgId}/model-setting`, {
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
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  const send = async (text: string) => {
    await page.getByLabel("发给管理助手的消息").fill(text)
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      page
        .getByRole("log")
        .getByRole("region", { name: "设置改动确认卡" })
        .last(),
    ).toBeVisible({ timeout: 30000 })
    await expect(page.getByLabel("发给管理助手的消息")).toBeEnabled()
  }
  await send("把提醒改成每年一次，证据期限改为36个月")
  await page.getByRole("button", { name: "新对话", exact: true }).click()
  await expect(page.getByRole("log")).not.toContainText("把提醒改成每年一次")
  await send("第二个合成对话：关闭经理确认")
  await page.reload()
  await expect(page.getByRole("log")).toContainText("第二个合成对话")
  await page.getByRole("button", { name: "历史对话", exact: true }).click()
  await page
    .getByRole("region", { name: "历史对话" })
    .getByRole("button", { name: /把提醒改成每年一次/ })
    .click()
  await expect(page.getByRole("log")).not.toContainText("第二个合成对话")
  await send("继续第一个对话")
  await page.goto("/admin/rules?tab=reminders")
  const section = page.getByRole("region", { name: "持续更新提醒" })
  await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("36")
  await page.getByRole("button", { name: "打开管理助手" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("log")).toContainText("继续第一个对话")
  await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
  await dialog
    .getByRole("region", { name: "历史对话" })
    .getByRole("button", { name: /第二个合成对话/ })
    .click()
  await expect(dialog.getByRole("log")).toContainText("第二个合成对话")
  await page.keyboard.press("Escape")
  await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("24")
  await expectChineseCopy(page)
})
