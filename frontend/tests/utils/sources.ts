import { expect, type Page } from "@playwright/test"
import type {
  AccountsPublic,
  CsvImportPublic,
  ExternalIdentitiesPublic,
  SyncRunsPublic,
} from "../../src/client"

export async function addDraftingEvidence(page: Page) {
  const { base, headers } = await page.evaluate(() => ({
    base: `/api/v1/orgs/${localStorage.getItem("current_org_id")}`,
    headers: {
      Authorization: `Bearer ${localStorage.getItem("access_token")}`,
    },
  }))
  const accounts: AccountsPublic = await (
    await page.request.get(
      `${base}/accounts?employment_status=active&limit=100`,
      { headers },
    )
  ).json()
  // Every expected initial draft needs supporting work, including employees absent from Jira.
  const imported = await page.request.post(`${base}/sources/csv/imports`, {
    headers,
    multipart: {
      file: {
        name: "drafting-evidence.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          [
            "record_id,source,occurred_at,actor_account,title",
            ...accounts.data.map(
              (employee) =>
                `${employee.id},E2E,2020-01-01T00:00:00Z,${employee.id},合成工作记录`,
            ),
          ].join("\n"),
        ),
      },
    },
  })
  expect(imported.status()).toBe(202)
  const { run }: CsvImportPublic = await imported.json()
  await expect
    .poll(
      async () => {
        const runs: SyncRunsPublic = await (
          await page.request.get(`${base}/sync-runs`, { headers })
        ).json()
        return runs.data.find((row) => row.id === run.id)?.status
      },
      { timeout: 60000 },
    )
    .toBe("succeeded")
  const identities: ExternalIdentitiesPublic = await (
    await page.request.get(`${base}/external-identities?status=all&limit=200`, {
      headers,
    })
  ).json()
  const links = identities.data
    .filter((identity) => identity.source === "csv:E2E")
    .map((identity) => ({
      identity_id: identity.id,
      employee_id: identity.external_id,
    }))
  expect(links).toHaveLength(accounts.data.length)
  const linked = await page.request.post(`${base}/external-identities/link`, {
    headers,
    data: { links },
  })
  expect(linked.ok()).toBeTruthy()
  expect((await linked.json()).linked).toBe(links.length)
}

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
  await page.goto("/admin/data-ai?tab=sources")
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

export async function setupStep(page: Page, key: string, action = "complete") {
  const tenant = await page.evaluate(() => ({
    base: `/api/v1/orgs/${localStorage.getItem("current_org_id")}`,
    headers: {
      Authorization: `Bearer ${localStorage.getItem("access_token")}`,
    },
  }))
  return page.request.post(`${tenant.base}/setup/steps/${key}/${action}`, {
    headers: tenant.headers,
  })
}

export async function activateTemplate(page: Page, templateId: string) {
  const tenant = await page.evaluate(() => ({
    base: `/api/v1/orgs/${localStorage.getItem("current_org_id")}`,
    headers: {
      Authorization: `Bearer ${localStorage.getItem("access_token")}`,
    },
  }))
  expect(
    (
      await page.request.post(`${tenant.base}/capability-schema`, {
        headers: tenant.headers,
        data: { template_id: templateId },
      })
    ).ok(),
  ).toBeTruthy()
}
