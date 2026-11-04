import { createHash } from "node:crypto"
import { type APIRequestContext, expect, test } from "@playwright/test"
import { safeNext } from "../src/utils"
import { expectChineseCopy } from "./utils/copy"

const password = "synthetic-oauth-password-2026"
const verifier = "a".repeat(43)
const callback = "https://client.example/callback"
test.use({ storageState: { cookies: [], origins: [] } })

async function prepare(request: APIRequestContext, enabled = true) {
  const email = `oauth-${crypto.randomUUID()}@example.com`
  const setup = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setup.json()).token,
      org_name: "合成授权测试企业",
      admin_username: `admin-${email}`,
      admin_full_name: "测试管理员",
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}`
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\n001,测试员工,研发部,,,${email}\n`,
        ),
      },
    },
  })
  const preview = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  const reset = await request.post(
    `${base}/accounts/${accounts.data[0].account.user_id}/reset-password`,
    { headers },
  )
  const initialPassword = (await reset.json()).initial_password
  expect(
    (
      await request.put(`${base}/mcp-setting`, { headers, data: { enabled } })
    ).ok(),
  ).toBeTruthy()
  const registered = await request.post("/oauth/register", {
    data: {
      client_name: "合成测试助手",
      redirect_uris: [callback],
      token_endpoint_auth_method: "none",
    },
  })
  expect(registered.ok()).toBeTruthy()
  const client = await registered.json()
  const metadata = await (
    await request.get("/.well-known/oauth-protected-resource")
  ).json()
  const params = new URLSearchParams({
    client_id: client.client_id,
    response_type: "code",
    redirect_uri: callback,
    code_challenge: createHash("sha256").update(verifier).digest("base64url"),
    code_challenge_method: "S256",
    state: "synthetic-state",
    resource: metadata.resource,
  })
  const authorized = await request.get(`/oauth/authorize?${params}`, {
    maxRedirects: 0,
  })
  expect(authorized.status()).toBe(302)
  const consent = new URL(authorized.headers().location)
  return {
    email,
    initialPassword,
    client,
    org_id,
    resource: metadata.resource,
    consent: consent.pathname + consent.search,
  }
}

for (const action of ["允许", "拒绝", "企业关闭"] as const) {
  test(`邮箱登录和首次改密后回到授权页：${action}`, async ({
    page,
    request,
  }, testInfo) => {
    const info = await prepare(request, action !== "企业关闭")
    await page.goto(info.consent)
    await expect(page).toHaveURL(/\/login\?next=/)
    await page.getByTestId("email-input").fill(info.email)
    await page.getByTestId("password-input").fill(info.initialPassword)
    await page.getByRole("button", { name: "登录", exact: true }).click()
    await expect(page).toHaveURL(/\/change-password\?next=/)
    await page
      .getByLabel("当前密码", { exact: true })
      .fill(info.initialPassword)
    await page.getByLabel("新密码", { exact: true }).fill(password)
    await page.getByLabel("确认新密码", { exact: true }).fill(password)
    await page.getByRole("button", { name: "保存密码", exact: true }).click()
    await expect(page).toHaveURL(/\/oauth\/consent\?request=/)
    await expect(
      page.getByText("名称由客户端自己填写", { exact: true }),
    ).toBeVisible()
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "合成测试助手",
    )
    await expectChineseCopy(page, ["client", "example"])
    await page.screenshot({
      path: testInfo.outputPath("oauth-consent.png"),
      fullPage: true,
    })
    if (action === "企业关闭") {
      await expect(page.getByRole("alert")).toContainText("企业已关闭")
      await expect(
        page.getByRole("button", { name: "允许", exact: true }),
      ).toHaveCount(0)
      return
    }
    await page.route(`${callback}**`, (route) =>
      route.fulfill({
        contentType: "text/html",
        body: "<p>已返回测试客户端</p>",
      }),
    )
    await page.getByRole("button", { name: action, exact: true }).click()
    await expect(page).toHaveURL(/https:\/\/client.example\/callback\?/)
    const result = new URL(page.url()).searchParams
    expect(result.get("state")).toBe("synthetic-state")
    if (action === "拒绝") {
      expect(result.get("error")).toBe("access_denied")
      expect(result.has("code")).toBeFalsy()
      return
    }
    expect(result.get("code")).toBeTruthy()
    const pairResponse = await request.post("/oauth/token", {
      form: {
        grant_type: "authorization_code",
        client_id: info.client.client_id,
        code: result.get("code")!,
        redirect_uri: callback,
        code_verifier: verifier,
      },
    })
    expect(pairResponse.ok()).toBeTruthy()
    const pair = await pairResponse.json()
    const mcpHeaders = {
      Authorization: `Bearer ${pair.access_token}`,
      Host: new URL(info.resource).host,
      Accept: "application/json, text/event-stream",
    }
    expect(
      (
        await request.post("/mcp", {
          headers: mcpHeaders,
          data: {
            jsonrpc: "2.0",
            id: 1,
            method: "initialize",
            params: {
              protocolVersion: "2025-03-26",
              capabilities: {},
              clientInfo: { name: "合成测试助手", version: "1.0" },
            },
          },
        })
      ).ok(),
    ).toBeTruthy()
    await page.goto("/settings")
    await page.getByRole("tab", { name: "连接 AI 助手" }).click()
    const grants = page.getByRole("region", { name: "已授权的客户端" })
    await expect(
      grants.getByText("合成测试助手", { exact: true }),
    ).toBeVisible()
    await expect(grants).toContainText("授权时间")
    await grants.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(grants.getByText("合成测试助手", { exact: true })).toHaveCount(
      0,
    )
    expect(
      (await request.post("/mcp", { headers: mcpHeaders, data: {} })).status(),
    ).toBe(401)
  })
}

for (const invalid of [
  "https://evil.example",
  "//evil.example",
  "/\\evil.example",
  "/\nevil.example",
  "/a/..//evil.example",
  "javascript:alert(1)",
  "relative",
]) {
  test(`拒绝站外返回路径 ${JSON.stringify(invalid)}`, () => {
    expect(safeNext(invalid)).toBe("/")
  })
}
test("保留站内授权返回路径和查询参数", () => {
  expect(safeNext("/oauth/consent?request=jwt")).toBe(
    "/oauth/consent?request=jwt",
  )
})
