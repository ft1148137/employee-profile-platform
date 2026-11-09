import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("model kinds, saved secret, destination changes and disabling preserve configuration", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(60000)
  await page.setViewportSize({ width: 375, height: 812 })
  const initialized = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const setup = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await initialized.json()).token,
      org_name: "模型设置合成企业",
      admin_username: `model-${Date.now()}@example.com`,
      admin_password: "synthetic-model-password",
    },
  })
  expect(setup.ok()).toBeTruthy()
  const { access_token, org_id } = await setup.json()
  await page.goto("/login")
  await page.evaluate(
    (token) => localStorage.setItem("access_token", token),
    access_token,
  )
  await page.goto("/admin/model")
  await page.getByLabel("启用模型").check()
  await page.getByLabel("模型类型").selectOption("cloud_api")
  await expect(page.getByText(/Claude 需要 Anthropic API key/)).toBeVisible()
  await expect(
    page.getByRole("option", { name: "Claude 订阅", exact: true }),
  ).toHaveCount(0)
  await page.getByLabel("提供方", { exact: true }).selectOption("openai")
  await expect(page.getByLabel(/base URL/)).toHaveCount(0)
  await page.getByLabel("模型类型").selectOption("openai_compatible")
  await page.getByLabel("模型名", { exact: true }).fill("synthetic")
  await page
    .getByLabel("密钥（可选）", { exact: true })
    .fill("synthetic-model-key")
  await page.getByLabel(/base URL/).fill(modelUrl)
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/model-setting") && r.request().method() === "PUT",
  )
  await page.getByRole("button", { name: "保存", exact: true }).click()
  const body = await (await response).text()
  expect(body).not.toContain("synthetic-model-key")
  expect(body).not.toContain("ciphertext")
  await page.reload()
  await expect(page.getByPlaceholder("已保存，留空表示不修改")).toBeVisible()
  await page.screenshot({
    path: "test-results/model-setting.png",
    fullPage: true,
  })
  await page.getByRole("button", { name: "测试连接" }).click()
  await expect(page.getByText("模型连接成功", { exact: true })).toBeVisible({
    timeout: 45000,
  })
  await page.getByLabel("启用模型").uncheck()
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(page.getByText("模型设置已保存。")).toBeVisible()
  const saved = await request.get(`/api/v1/orgs/${org_id}/model-setting`, {
    headers: { Authorization: `Bearer ${access_token}` },
  })
  expect(await saved.json()).toMatchObject({
    enabled: false,
    kind: "openai_compatible",
    provider: "openai-compatible",
    model: "synthetic",
    base_url: modelUrl,
    has_api_key: true,
  })
  await page.getByLabel("模型类型").selectOption("local")
  await expect(page.getByLabel(/base URL/)).toBeVisible()
  await expect(page.getByText("需要重新填写密钥")).toBeVisible()
  await expect(page.getByLabel("密钥（可选）")).toHaveAttribute("required", "")
  await page.getByLabel("模型类型").selectOption("openai_compatible")
  await expect(page.getByLabel("base URL（填到 /v1）")).toBeVisible()
  await page.getByLabel("模型类型").selectOption("chatgpt_subscription")
  await expect(page.getByLabel("模型名", { exact: true })).toHaveValue(
    "chatgpt/gpt-6.1-sol",
  )
  await expect(page.getByLabel("推理强度")).toHaveValue("high")
  await expect(page.getByLabel(/密钥/)).toHaveCount(0)
  await page
    .getByLabel("模型名", { exact: true })
    .fill("chatgpt/gpt-6.1-sol-custom")
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(page.getByText("模型设置已保存。")).toBeVisible()
  await page.reload()
  await expect(page.getByLabel("模型名", { exact: true })).toHaveValue(
    "chatgpt/gpt-6.1-sol-custom",
  )
  await expect(page.getByLabel("推理强度")).toHaveValue("high")
})

test("ChatGPT authorization callback, connection and disconnect", async ({
  page,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  let started = false
  let disconnected = false
  await page.route("**/model-setting/chatgpt/login", async (route) => {
    if (route.request().method() === "POST") started = true
    const pending = {
      status: "pending",
      verification_url: "https://auth.openai.com/oauth/authorize?state=test",
      expires_at: new Date(Date.now() + 900000).toISOString(),
    }
    const json = !started || disconnected ? { status: "none" } : pending
    await route.fulfill({ json })
  })
  await page.route("**/model-setting/chatgpt/callback", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      input: "http://localhost:1455/auth/callback?code=test&state=test",
    })
    await route.fulfill({
      json: {
        status: "connected",
        account: {
          email: "synthetic@example.com",
          plan_type: "business",
          needs_relogin: false,
        },
      },
    })
  })
  await page.route("**/model-setting/chatgpt", async (route) => {
    disconnected = true
    await route.fulfill({ json: { ok: true, message: "已断开连接" } })
  })
  await page.goto("/admin/model")
  await page.getByLabel("模型类型").selectOption("chatgpt_subscription")
  for (const text of [
    "官方计划",
    "每周两种用量上限",
    "数据控制",
    "建议用 Business/Enterprise",
  ])
    await expect(page.getByText(text, { exact: false })).toBeVisible()
  await page.getByRole("button", { name: "连接 ChatGPT", exact: true }).click()
  await expect(
    page.getByRole("link", { name: "打开 ChatGPT 授权页面" }),
  ).toBeVisible()
  await page
    .getByLabel("重定向网址")
    .fill("http://localhost:1455/auth/callback?code=test&state=test")
  await page.getByRole("button", { name: "完成连接" }).click()
  await expect(
    page.getByText(/已连接 ChatGPT：synthetic@example.com/),
  ).toBeVisible({
    timeout: 15000,
  })
  await expect(page.getByText(/令牌在 OpenAI 那边不会失效/)).toBeVisible()
  await page.getByRole("button", { name: "断开连接" }).click()
  await expect(
    page.getByRole("button", { name: "连接 ChatGPT", exact: true }),
  ).toBeVisible()
})

for (const role of ["manager", "employee"])
  test(`${role} cannot access model settings`, async ({ page }) => {
    await logInUser(
      page,
      `${role}.platform@demo.example.com`,
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(
      page.getByRole("link", { name: "数据与 AI", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/model")
    await expect(page.getByRole("alert")).toHaveText("需要企业管理员权限")
    await expect(page.getByLabel("模型类型")).toHaveCount(0)
  })
