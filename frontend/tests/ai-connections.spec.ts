import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

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
    await page.getByRole("link", { name: "外部 AI 助手", exact: true }).click()
    await expect(
      page.getByText(
        "打开后，员工的画像、证据和自述会发给外部助手的厂商。请先确认企业允许这些数据外发。",
      ),
    ).toBeVisible()
    await expect(
      page.getByText("按客户端自报的名称识别，不是安全边界，名称可以伪造。"),
    ).toBeVisible()
    await page.getByRole("checkbox", { name: "允许外部 AI 助手" }).check()
    await page.getByLabel("允许的客户端", { exact: true }).fill("Cursor")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toHaveText("已保存")
    await page.reload()
    await expect(page.getByRole("checkbox")).toBeChecked()
    await expect(page.getByLabel("允许的客户端", { exact: true })).toHaveValue(
      "Cursor",
    )
    await logInUser(employee, "employee.platform@demo.example.com", password)
    const accessToken = await employee.evaluate(() =>
      localStorage.getItem("access_token"),
    )
    employeeHeaders = { Authorization: `Bearer ${accessToken}` }
    await employee.goto("/settings")
    await employee.getByRole("tab", { name: "连接 AI 助手" }).click()
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
    await expect(employee.getByTestId("cursor-config")).toContainText(
      `Bearer ${data.token}`,
    )
    await expect(employee.getByTestId("cursor-config")).toContainText(
      `${new URL(employee.url()).origin}/mcp`,
    )
    await employee.getByRole("button", { name: "已保存，隐藏令牌" }).click()
    await expect(employee.getByTestId("new-token")).toHaveCount(0)
    await expect(employee.getByTestId("cursor-config")).not.toContainText(
      data.token,
    )
    await expectChineseCopy(employee, [
      "AI",
      "Cursor",
      "MCP",
      "mcpServers",
      "employeeProfile",
      "url",
      "headers",
      "Authorization",
      "Bearer",
      data.prefix,
    ])
    await employee.reload()
    await employee.getByRole("tab", { name: "连接 AI 助手" }).click()
    await expect(employee.getByTestId("new-token")).toHaveCount(0)
    await expect(employee.getByText(name, { exact: true })).toBeVisible()
    await page.getByRole("checkbox", { name: "允许外部 AI 助手" }).uncheck()
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toHaveText("已保存")
    await employee.reload()
    await employee.getByRole("tab", { name: "连接 AI 助手" }).click()
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
