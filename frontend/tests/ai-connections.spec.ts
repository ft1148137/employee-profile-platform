import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

const httpNotice =
  "当前为 HTTP 地址，外部工具连接需使用 HTTPS 正式地址，请联系部署人员获取。"

test("HTTPS connection examples do not show the local HTTP notice", async ({
  page,
  baseURL,
}) => {
  await page.route("https://platform.example.test/**", async (route) => {
    const url = new URL(route.request().url())
    const response = await route.fetch({
      url: `${baseURL}${url.pathname}${url.search}`,
    })
    await route.fulfill({ response })
  })
  try {
    await page.goto("https://platform.example.test/login")
    await page
      .getByTestId("email-input")
      .fill("employee.platform@demo.example.com")
    await page
      .getByTestId("password-input")
      .fill(process.env.DEMO_USER_PASSWORD!)
    await page.getByRole("button", { name: "登录", exact: true }).click()
    await expect(page.getByTestId("user-menu")).toBeVisible()
    await page.goto("https://platform.example.test/settings")
    await page.getByRole("link", { name: "连接与集成" }).click()
    await expect(page.getByTestId("cursor-config")).toHaveCount(0)
    await page.getByRole("button", { name: "查看 MCP 接入配置" }).click()
    await expect(page.getByTestId("cursor-config")).toContainText(
      "https://platform.example.test/mcp",
    )
    await expect(page.getByText(httpNotice)).toHaveCount(0)
  } finally {
    await page.unrouteAll({ behavior: "wait" })
  }
})

test("enterprise switch and personal token lifecycle", async ({
  page,
  browser,
  request,
}) => {
  const password = process.env.DEMO_USER_PASSWORD!
  await logInUser(page, "admin@demo.example.com", password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const base = `/api/v1/orgs/${memberships[0].org.id}`
  const original = await (
    await request.get(`${base}/mcp-setting`, { headers })
  ).json()
  const employee = await browser.newPage({
    baseURL: new URL(page.url()).origin,
    storageState: { cookies: [], origins: [] },
  })
  const name = `工作电脑测试${Date.now()}`
  let id = ""
  let employeeHeaders = {}
  try {
    expect(
      (
        await request.put(`${base}/mcp-setting`, {
          headers,
          data: { enabled: false, allowed_clients: [] },
        })
      ).ok(),
    ).toBeTruthy()
    await page.getByRole("link", { name: "设置", exact: true }).click()
    await page.getByRole("link", { name: "外部 AI 工具", exact: true }).click()
    await expect(
      page.getByText(
        "打开后，员工的画像、证据和自述会发给外部助手的厂商。请先确认企业允许这些数据外发。",
      ),
    ).toBeVisible()
    const configure = page.getByRole("button", {
      name: "配置 外部 AI 工具",
      exact: true,
    })
    await configure.click()
    await expect(
      page.getByText("按客户端自报的名称识别，不是安全边界，名称可以伪造。"),
    ).toBeVisible()
    await page.getByRole("checkbox", { name: "允许接入外部 AI 工具" }).check()
    await page.getByLabel("允许的客户端", { exact: true }).fill("Cursor")
    await expect(
      page.getByRole("button", { name: "保存", exact: true }),
    ).toBeDisabled()
    await page
      .getByRole("checkbox", {
        name: "已确认企业允许向外部工具发送画像、证据和自述",
      })
      .check()
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toHaveText("已保存")
    await page.reload()
    await configure.click()
    await expect(
      page.getByRole("checkbox", { name: "允许接入外部 AI 工具" }),
    ).toBeChecked()
    await expect(page.getByLabel("允许的客户端", { exact: true })).toHaveValue(
      "Cursor",
    )
    await logInUser(employee, "employee.platform@demo.example.com", password)
    const accessToken = await employee.evaluate(() =>
      localStorage.getItem("access_token"),
    )
    employeeHeaders = { Authorization: `Bearer ${accessToken}` }
    await employee.goto("/settings")
    await employee.getByRole("link", { name: "连接与集成" }).click()
    const tokenScope = employee.getByRole("region", { name: "个人令牌与画像" })
    await expect(tokenScope).toContainText(
      "读取本人画像、可见证据和画像变更请求",
    )
    await expect(tokenScope).toContainText("管辖经理也可查看管辖团队摘要")
    await expect(tokenScope).toContainText("记录提交来源和客户端")
    await expect(tokenScope).toContainText("需审批的改动可在经理批准前撤回")
    await expect(tokenScope).toContainText("权限受你本人账号的限制")
    await expect(tokenScope).toContainText("可随时在本页「我的令牌」中吊销")
    await employee.getByLabel("令牌名称").fill(name)
    const created = employee.waitForResponse(
      (response) =>
        response.url().endsWith("/me/access-tokens") &&
        response.request().method() === "POST",
    )
    await employee.getByRole("button", { name: "生成令牌" }).click()
    const response = await created
    expect(response.status()).toBe(201)
    const data = await response.json()
    id = data.id
    await expect(employee.getByTestId("new-token")).toHaveText(data.token)
    await expect(employee.getByTestId("cursor-config")).toHaveCount(0)
    const configButton = employee.getByRole("button", {
      name: "查看 MCP 接入配置",
    })
    const configDialog = employee.getByRole("dialog", {
      name: "查看 MCP 接入配置",
    })
    await configButton.click()
    await expect(configDialog.getByText(httpNotice)).toBeVisible()
    await expect(employee.getByTestId("cursor-config")).toContainText(
      `Bearer ${data.token}`,
    )
    await expect(employee.getByTestId("cursor-config")).toContainText(
      `${new URL(employee.url()).origin}/mcp`,
    )
    await configDialog.getByRole("button", { name: "关闭" }).click()
    await employee.getByRole("button", { name: "已保存，隐藏令牌" }).click()
    await configButton.click()
    await expect(employee.getByTestId("new-token")).toHaveCount(0)
    await expect(employee.getByTestId("cursor-config")).not.toContainText(
      data.token,
    )
    await expect(employee.getByTestId("cursor-config")).toContainText(
      "Bearer <令牌>",
    )
    await employee
      .context()
      .grantPermissions(["clipboard-read", "clipboard-write"])
    await configDialog.getByRole("button", { name: "复制配置" }).click()
    await expect(configDialog.getByRole("status")).toHaveText("已复制")
    const copied = await employee.evaluate(() => navigator.clipboard.readText())
    expect(copied).toBe(
      await employee.getByTestId("cursor-config").textContent(),
    )
    await employee.keyboard.press("Escape")
    await expect(configDialog).toBeHidden()
    await expect(configButton).toBeFocused()
    await expectChineseCopy(
      employee,
      [
        "AI",
        "Cursor",
        "Telegram",
        "skill",
        "MCP",
        "mcpServers",
        "employeeProfile",
        "url",
        "headers",
        "Authorization",
        "Bearer",
        ...(await employee
          .getByRole("group")
          .locator("code")
          .allTextContents()),
      ],
      ["demo-jira-02"],
    )
    await employee.reload()
    await employee.getByRole("link", { name: "连接与集成" }).click()
    await expect(employee.getByTestId("new-token")).toHaveCount(0)
    await expect(employee.getByText(name, { exact: true })).toBeVisible()
    await page.getByRole("checkbox", { name: "允许接入外部 AI 工具" }).uncheck()
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toHaveText("已保存")
    await employee.reload()
    await employee.getByRole("link", { name: "连接与集成" }).click()
    await expect(
      employee.getByText(
        "企业未开启此功能，请联系管理员在「设置 → 外部 AI 工具」中开启（已有令牌仍可查看或吊销）。",
      ),
    ).toBeVisible()
    await expect(
      employee.getByRole("button", { name: "生成令牌" }),
    ).toBeDisabled()
    await employee
      .getByRole("group", { name, exact: true })
      .getByRole("button", { name: "吊销", exact: true })
      .click()
    await expect(
      employee
        .getByRole("group", { name, exact: true })
        .getByRole("button", { name: "吊销", exact: true }),
    ).toBeDisabled()
    await expect(
      employee.getByText("已吊销", { exact: false }).first(),
    ).toBeVisible()
    await employee.screenshot({
      path: "test-results/ai-connections.png",
      fullPage: true,
    })
  } finally {
    if (id)
      expect(
        (
          await request.delete(`${base}/me/access-tokens/${id}`, {
            headers: employeeHeaders,
          })
        ).ok(),
      ).toBeTruthy()
    expect(
      (
        await request.put(`${base}/mcp-setting`, {
          headers,
          data: {
            enabled: original.enabled,
            allowed_clients: original.allowed_clients,
          },
        })
      ).ok(),
    ).toBeTruthy()
    await employee.close()
  }
})
