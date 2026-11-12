import type { Page } from "@playwright/test"
import { mockAdminMessages } from "./utils/adminAssistant"
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
  await expect(page).toHaveURL("/admin/assistant")
  await expect(
    page.getByRole("region", { name: "开始使用", exact: true }),
  ).toBeVisible()
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
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试企业更新")
  await page.getByRole("button", { name: "保存企业名称", exact: true }).click()
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await expect(
    page.getByText("模型已就绪，接下来我带你完成剩下的设置"),
  ).toBeVisible()
  const saveReply = await mockAdminMessages(page)
  const sent: string[] = []
  await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
    sent.push(route.request().postDataJSON().text)
    const reply = {
      messages: [{ type: "text", text: "先导入员工名单，请上传名单材料。" }],
    }
    saveReply(route.request().url(), reply)
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: reply })}\n`,
    })
  })
  await page.getByRole("button", { name: "开始", exact: true }).click()
  await expect(
    page.getByText("先导入员工名单，请上传名单材料。").last(),
  ).toBeVisible()
  expect(sent).toEqual(["帮我完成首次设置"])
  await page.screenshot({ path: testInfo.outputPath("setup-assistant.png") })
  await page.goto("/admin/assistant")
  await expect(
    page.getByRole("region", { name: "开始使用", exact: true }),
  ).toHaveCount(0)
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
  await expect(page).toHaveURL(/\/admin\/assistant$/)
  const todo = page
    .getByRole("listitem")
    .filter({ hasText: "步首次设置没完成" })
  await expect(todo).toBeVisible()
  await todo.getByRole("link", { name: "去处理", exact: true }).click()
  await expect(page).toHaveURL(/\/admin\/assistant\?prompt=/)
  await expect(
    page.getByRole("textbox", { name: "发给管理助手的消息" }),
  ).toHaveValue("帮我完成首次设置")
  await expect(page.getByText("模型未配置或不可用。")).toBeVisible()
  await expect(
    page.getByRole("link", { name: "去配置模型", exact: true }),
  ).toHaveAttribute("href", "/admin/data-ai?tab=model")
  await expect(
    page.getByRole("region", { name: "开始使用", exact: true }),
  ).toHaveCount(0)
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
  await expect(
    page.getByRole("region", { name: "开始使用", exact: true }),
  ).toBeVisible()
})
