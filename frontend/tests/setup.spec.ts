import type { Page } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { configureModel, expect, test } from "./utils/model"

test.use({ storageState: { cookies: [], origins: [] } })
test.describe.configure({ mode: "serial" })
const invalidLink =
  "初始化链接无效或已过期，请联系部署人员重新运行安装脚本获取新链接"

async function fillSetup(page: Page) {
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试医院")
  await page
    .getByLabel("管理员邮箱", { exact: true })
    .fill(`setup-${crypto.randomUUID()}@example.com`)
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password-2026")
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-2026")
}

async function initialize(page: Page) {
  const response = await page.request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  const { token } = await response.json()
  await page.goto(`/setup#token=${token}`)
  await fillSetup(page)
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page).toHaveURL(/\/admin\/assistant\?session=/)
  await expect(page.getByRole("list", { name: "设置步骤" })).toBeVisible()
  await page.getByText("企业名称 · 已完成（展开修改）", { exact: true }).click()
  return token
}

test("initialize, configure model and start assistant setup guide", async ({
  page,
  modelUrl,
}, testInfo) => {
  await initialize(page)
  const steps = page.getByRole("list", { name: "设置步骤" })
  await expect(steps.getByRole("listitem")).toHaveCount(9)
  await expect(steps).toContainText("待做")
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("模型")
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试企业更新")
  await page.getByRole("button", { name: "保存企业名称", exact: true }).click()
  await expect(
    page.getByRole("link", { name: /首次设置 · .* 步待完成/ }).first(),
  ).toBeVisible()
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await expect(page.getByText("配置模型 · 已完成（展开修改）")).toBeVisible()
  const sessionUrl = page.url()
  let savedReply: unknown
  await page.route(
    "**/admin-assistant/sessions/*/messages?*",
    async (route) => {
      const response = await route.fetch()
      const initial = await response.json()
      await route.fulfill({
        json: savedReply
          ? [
              ...initial,
              {
                id: crypto.randomUUID(),
                role: "assistant",
                payload: savedReply,
                created_at: new Date().toISOString(),
              },
            ]
          : initial,
      })
    },
  )
  const sent: string[] = []
  await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
    sent.push(route.request().postDataJSON().text)
    const reply = {
      messages: [{ type: "text", text: "先导入员工名单，请上传名单材料。" }],
    }
    expect(route.request().url()).toContain(
      new URL(sessionUrl).searchParams.get("session")!,
    )
    savedReply = reply
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: reply })}\n`,
    })
  })
  await page
    .getByRole("button", { name: "跳过接数据来源", exact: true })
    .click()
  await expect(
    steps.getByRole("listitem").filter({ hasText: "接数据来源" }),
  ).toContainText("已跳过")
  await page.reload()
  await expect(page).toHaveURL(sessionUrl)
  await expect(steps).toBeVisible()
  await page
    .getByRole("button", {
      name: "继续，让助手带我完成剩下的设置",
      exact: true,
    })
    .click()
  await expect(
    page.getByText("先导入员工名单，请上传名单材料。").last(),
  ).toBeVisible()
  expect(sent).toEqual(["帮我完成首次设置"])
  await page.screenshot({ path: testInfo.outputPath("setup-assistant.png") })
  await page.goto("/admin/assistant")
  await expect(page.getByRole("list", { name: "设置步骤" })).toHaveCount(0)
  await expect(
    page.getByRole("heading", { name: "你好，今天有什么要处理的？" }),
  ).toBeVisible()
})

test("initialization validation and persisted enterprise name at 375px", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const token = await initialize(page)
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试医院更新")
  await page.getByRole("button", { name: "保存企业名称", exact: true }).click()
  await expect(
    page.getByRole("img", { name: "合成测试医院更新" }),
  ).toBeVisible()
  await page.reload()
  await page.getByText("企业名称 · 已完成（展开修改）", { exact: true }).click()
  await expect(page.getByLabel("企业名称", { exact: true })).toHaveValue(
    "合成测试医院更新",
  )
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.screenshot({
    path: testInfo.outputPath("setup-start-mobile.png"),
    fullPage: true,
  })
  await page.goto(`/setup#token=${token}`)
  await fillSetup(page)
  const email = page.getByLabel("管理员邮箱", { exact: true })
  const validEmail = await email.inputValue()
  await email.fill("invalid-email")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  expect(
    await email.evaluate((input: HTMLInputElement) => input.validity.valid),
  ).toBe(false)
  await email.fill(validEmail)
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
})

test("missing and invalid initialization links and uninitialized login hint", async ({
  page,
}) => {
  await page.goto("/setup")
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
  await page.goto("/setup#token=invalid")
  await fillSetup(page)
  await page.getByLabel("确认密码", { exact: true }).fill("different-password")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText("两次输入的密码不一致")
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-2026")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
  await page.route("**/setup/status", (route) =>
    route.fulfill({ json: { initialized: false } }),
  )
  await page.goto("/login")
  await expect(page.getByRole("status")).toContainText("系统还没有初始化")
  await expectChineseCopy(page)
})

test("skip model, stay in assistant and resume from setup todo", async ({
  page,
}) => {
  await initialize(page)
  await page.getByRole("button", { name: "暂不配置模型", exact: true }).click()
  const sessionUrl = page.url()
  await expect(
    page.getByText("模型未配置，配置后回到这条对话继续。"),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: "数据与 AI → 模型", exact: true }),
  ).toHaveAttribute("href", "/admin/data-ai?tab=model")
  await page.reload()
  await expect(page).toHaveURL(sessionUrl)
  await expect(page.getByRole("list", { name: "设置步骤" })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "完成模型配置", exact: true }),
  ).toBeVisible()
  await page.goto("/admin/assistant")
  const todo = page
    .getByRole("listitem")
    .filter({ hasText: "步首次设置没完成" })
  await todo.getByRole("link", { name: "去处理", exact: true }).click()
  await expect(page).toHaveURL(sessionUrl)
  await page.goto("/setup-wizard")
  await expect(page).toHaveURL("/admin/assistant")
  await page.getByRole("button", { name: "更多", exact: true }).click()
  await expect(
    page.getByRole("menuitem", { name: "首次设置", exact: false }),
  ).toHaveCount(0)
})

test("enterprise save failure keeps setup editable", async ({ page }) => {
  await initialize(page)
  await page.route("**/api/v1/orgs/*", (route) =>
    route.fulfill({
      status: 500,
      json: { detail: "保存失败，请重试" },
    }),
  )
  await page.getByLabel("企业名称", { exact: true }).fill("合成更新")
  await page.getByRole("button", { name: "保存企业名称", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("保存失败，请重试")
  await expect(page.getByLabel("企业名称", { exact: true })).toBeEnabled()
  await expect(page.getByRole("list", { name: "设置步骤" })).toBeVisible()
})
