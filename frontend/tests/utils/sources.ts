import { expect, type Page } from "@playwright/test"

export async function createSourcesOrg(page: Page) {
  const response = await page.request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  const initialized = await page.request.post("/api/v1/setup/initialize", {
    data: {
      token: (await response.json()).token,
      org_name: "合成数据来源测试企业",
      admin_username: `sources-${crypto.randomUUID()}@example.com`,
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const data = await initialized.json()
  await page.goto("/login")
  await page.evaluate((result) => {
    localStorage.setItem("access_token", result.access_token)
    localStorage.setItem("current_org_id", result.org_id)
  }, data)
  await page.goto("/setup-wizard")
  await expect(page).toHaveURL("/setup-wizard")
  await page.getByText("稍后处理的事项", { exact: true }).click()
  await page
    .getByRole("navigation", { name: "设置步骤" })
    .getByRole("button", { name: /接数据来源/ })
    .click()
  await expect(page.getByRole("heading", { name: "连接 Jira" })).toBeVisible()
}

export async function saveCloud(
  page: Page,
  url: string,
  email: string,
  token: string,
) {
  await page.getByLabel("站点地址").fill(url)
  await page.getByLabel("账号邮箱").fill(email)
  await page.getByLabel("API token", { exact: true }).fill(token)
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
}

export async function syncJira(page: Page) {
  await page.getByRole("button", { name: "立即同步" }).click()
  await expect(page.getByText("Jira 同步：成功", { exact: true })).toBeVisible({
    timeout: 90000,
  })
}
