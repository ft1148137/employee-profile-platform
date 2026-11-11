import type { BrowserContext, Page } from "@playwright/test"
import type { SessionList } from "../src/client"
import { openAdminAssistant, returnToManualPage } from "./utils/adminAssistant"
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
  await expect(
    dialog.getByText("这段对话已过期被清除，已为你新开对话"),
  ).toHaveCount(0)
  await checkSwitching(page, context, false, "第二个合成对话")
})

test("admin resumes conversations across manual pages with matching page drafts", async ({
  page,
  request,
  modelUrl,
  context,
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
  await page.getByRole("link", { name: "新对话", exact: true }).click()
  await expect(page.getByRole("log")).not.toContainText("把提醒改成每年一次")
  await send("第二个合成对话：关闭经理确认")
  await page.reload()
  await expect(page.getByRole("log")).toContainText("第二个合成对话")
  await page
    .getByRole("region", { name: "管理助手会话" })
    .getByRole("link", { name: /把提醒改成每年一次/ })
    .click()
  await expect(page.getByRole("log")).not.toContainText("第二个合成对话")
  await send("继续第一个对话")
  const messageRequests: string[] = []
  const draftRequests: string[] = []
  page.on("request", (request) => {
    const url = new URL(request.url())
    if (/\/admin-assistant\/sessions\/[^/]+\/messages$/.test(url.pathname))
      messageRequests.push(url.href)
    if (url.pathname.endsWith("/admin-assistant/change-sets"))
      draftRequests.push(url.searchParams.get("session_id") ?? "")
  })
  await page.goto("/admin/rules?tab=reminders")
  const section = page.getByRole("region", { name: "持续更新提醒" })
  await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("36")
  expect(messageRequests).toEqual([])
  const currentId = await page.evaluate(
    () =>
      Object.entries(localStorage).find(([key]) =>
        key.startsWith("admin-assistant:"),
      )?.[1],
  )
  expect(draftRequests.length).toBeGreaterThan(0)
  expect(draftRequests.every((id) => id === currentId)).toBe(true)
  await openAdminAssistant(page)
  const dialog = page.getByRole("region", { name: "管理助手", exact: true })
  await expect(dialog.getByRole("log")).toContainText("继续第一个对话")
  await page
    .getByRole("region", { name: "管理助手会话" })
    .getByRole("link", { name: /第二个合成对话/ })
    .click()
  await expect(dialog.getByRole("log")).toContainText("第二个合成对话")
  await returnToManualPage(page)
  await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("24")
  await expectChineseCopy(page)
  await checkSwitching(page, context, true, "把提醒改成每年一次")
})

async function checkSwitching(
  page: Page,
  context: BrowserContext,
  administrator: boolean,
  expiredPreview: string,
) {
  const service = administrator ? "admin-assistant" : "bot"
  const opener = administrator ? "打开管理助手" : "打开画像机器人"
  const label = administrator ? "发给管理助手的消息" : "发给机器人的消息"
  const dialog = administrator
    ? page.getByRole("region", { name: "管理助手", exact: true })
    : page.getByRole("dialog")
  if (administrator) await openAdminAssistant(page)
  else if (!(await dialog.isVisible()))
    await page.getByRole("button", { name: opener }).click()
  const input = dialog.getByLabel(label)
  await expect(input).toBeEnabled()
  const tab = await context.newPage()
  await tab.goto(page.url())
  if (!administrator) await tab.getByRole("button", { name: opener }).click()
  await expect(tab.getByLabel(label)).toBeEnabled()
  await input.fill("本页还没发出的合成文字")
  const resumed = administrator
    ? page.waitForURL("/admin/assistant")
    : page.waitForResponse(
        (response) =>
          response.url().includes(`/${service}/sessions/`) &&
          response.url().includes("/messages?"),
      )
  await tab
    .getByRole(administrator ? "link" : "button", {
      name: "新对话",
      exact: true,
    })
    .click()
  await resumed
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("本页还没发出的合成文字")
  await tab.close()
  await (administrator ? page : dialog)
    .getByRole(administrator ? "link" : "button", {
      name: "新对话",
      exact: true,
    })
    .click()
  await expect(input).toHaveValue("")
  let history: SessionList
  if (administrator) {
    const token = await page.evaluate(() =>
      localStorage.getItem("access_token"),
    )
    const orgId = await page.evaluate(
      () =>
        Object.keys(localStorage)
          .find((key) => key.startsWith("admin-assistant:"))
          ?.split(":")[2],
    )
    history = await (
      await page.request.get(
        `/api/v1/admin-assistant/sessions?org_id=${orgId}`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
    ).json()
  } else {
    const listed = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === `/api/v1/${service}/sessions`,
    )
    await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
    history = await (await listed).json()
  }
  const expired = history.data.find((entry) =>
    entry.preview.includes(expiredPreview),
  )
  expect(expired).toBeTruthy()
  // Simulate retention cleanup after the history list was already displayed.
  let cleared = false
  await page.route(
    `**/${service}/sessions/${expired?.id}/messages?*`,
    (route) => {
      cleared = true
      return route.fulfill({ status: 404, json: {} })
    },
  )
  await page.route(`**/${service}/sessions?*`, async (route) => {
    if (route.request().method() !== "GET") return route.continue()
    const response = await route.fetch()
    const list: SessionList = await response.json()
    await route.fulfill({
      response,
      json: {
        ...list,
        data: list.data.filter((entry) => !cleared || entry.id !== expired?.id),
      },
    })
  })
  await input.fill("主动切换前的文字")
  await (administrator ? page : dialog)
    .getByRole("region", { name: administrator ? "管理助手会话" : "历史对话" })
    .getByRole(administrator ? "link" : "button", {
      name: new RegExp(expiredPreview),
    })
    .click()
  await expect(dialog.getByRole("status")).toHaveText(
    "这段对话已过期被清除，已为你新开对话",
  )
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue("")
  if (!administrator)
    await dialog.getByRole("button", { name: "历史对话", exact: true }).click()
  await expect(
    (administrator ? page : dialog)
      .getByRole("region", {
        name: administrator ? "管理助手会话" : "历史对话",
      })
      .getByRole(administrator ? "link" : "button", {
        name: new RegExp(expiredPreview),
      }),
  ).toHaveCount(0)
  await expectChineseCopy(page)
}
