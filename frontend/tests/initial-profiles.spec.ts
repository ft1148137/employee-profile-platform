import { readFileSync } from "node:fs"
import path from "node:path"
import type { ExternalIdentitiesPublic, SetupPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("new enterprise imports Jira, drafts in setup and shows the employee their draft", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  test.setTimeout(120000)
  const tokenResponse = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(tokenResponse.ok()).toBeTruthy()
  const username = `initial-${crypto.randomUUID()}`
  const password = "synthetic-password-2026"
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await tokenResponse.json()).token,
      org_name: "合成测试青砾软件",
      admin_username: `${username}@example.com`,
      admin_password: password,
      admin_full_name: "测试管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: token } = await initialized.json()
  const headers = { Authorization: `Bearer ${token}` }
  const base = `/api/v1/orgs/${orgId}`
  const uploaded = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: readFileSync(
          path.resolve(
            import.meta.dirname,
            "../../examples/test-enterprises/qingli-saas/roster.csv",
          ),
        ),
      },
    },
  })
  expect(uploaded.ok()).toBeTruthy()
  const preview = await uploaded.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.post(`${base}/capability-schema`, {
        headers,
        data: { template_id: "tech-rnd" },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.put(`${base}/sources/jira`, {
        headers,
        data: {
          deployment: "cloud",
          base_url: "http://mock-jira:8080/qingli-saas",
          auth_type: "api_token",
          username: "jira-admin@example.com",
          secret: "mock-cloud-token",
          start_date: "2020-01-01",
        },
      })
    ).ok(),
  ).toBeTruthy()
  const synced = await request.post(`${base}/sources/jira/sync`, { headers })
  expect(synced.status()).toBe(202)
  const run = await synced.json()
  await expect
    .poll(
      async () => {
        const runs = await (
          await request.get(`${base}/sync-runs`, { headers })
        ).json()
        return runs.data.find((row: { id: string }) => row.id === run.id)
          ?.status
      },
      { timeout: 60000 },
    )
    .toBe("succeeded")
  const identities: ExternalIdentitiesPublic = await (
    await request.get(`${base}/external-identities?limit=200`, { headers })
  ).json()
  expect(identities.count).toBeGreaterThan(0)
  const candidates = identities.data.filter((identity) => identity.candidate)
  expect(candidates.length).toBeGreaterThan(0)
  const links = candidates.map((identity) => ({
    identity_id: identity.id,
    employee_id: identity.candidate!.id,
  }))
  const linked = await request.post(`${base}/external-identities/link`, {
    headers,
    data: { links },
  })
  expect(linked.ok()).toBeTruthy()
  expect((await linked.json()).linked).toBe(links.length)
  expect(
    (
      await request.put(`${base}/model-setting`, {
        headers,
        data: {
          enabled: true,
          kind: "openai_compatible",
          model: "openai/synthetic",
          base_url: modelUrl,
          api_key: "synthetic-key",
        },
      })
    ).ok(),
  ).toBeTruthy()
  const setup: SetupPublic = await (
    await request.get(`${base}/setup`, { headers })
  ).json()
  for (const step of setup.steps.filter(
    (step) => step.status === "pending" && step.key !== "initial_profiles",
  )) {
    const action = "complete"
    expect(
      (
        await request.post(`${base}/setup/steps/${step.key}/${action}`, {
          headers,
        })
      ).ok(),
      step.key,
    ).toBeTruthy()
  }
  await logInUser(page, `${username}@example.com`, password)
  await expect(page).toHaveURL("/setup-wizard")
  await expect(
    page.getByRole("heading", { name: "生成初始画像", exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByRole("alert")).toContainText("名员工没有初始画像")
  await page.getByRole("button", { name: "生成初始画像", exact: true }).click()
  const accounts = await (
    await request.get(`${base}/accounts?employment_status=active&limit=100`, {
      headers,
    })
  ).json()
  const total = accounts.count
  expect(total).toBeGreaterThan(0)
  await expect(
    page.getByText(
      `已为 ${total} 名员工生成初始画像，员工登录后能在自己的画像页看到`,
    ),
  ).toBeVisible({ timeout: 60000 })
  await expectChineseCopy(page)
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible()
  await page.getByRole("link", { name: "同步与起草进度", exact: true }).click()
  const syncSection = page.getByRole("region", {
    name: "同步记录",
    exact: true,
  })
  await expect(syncSection.getByText("Jira", { exact: true })).toBeVisible()
  await expect(
    syncSection.getByRole("cell", { name: "已完成 1 次", exact: true }),
  ).toBeVisible()
  await expect(syncSection.getByText("记录：38", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("region", { name: "初始画像起草进度" }),
  ).toContainText(`已为 ${total} 名员工生成初始画像`)
  await page.screenshot({
    path: testInfo.outputPath("initial-profiles-progress.png"),
    fullPage: true,
  })
  const employee = identities.data.find(
    (identity) => identity.candidate?.employee_code === "QL003",
  )!.candidate!
  const account = accounts.data.find(
    (row: { id: string }) => row.id === employee.id,
  ).account
  const reset = await request.post(
    `${base}/accounts/${account.user_id}/reset-password`,
    { headers },
  )
  expect(reset.ok()).toBeTruthy()
  await logOutUser(page)
  await page.getByTestId("email-input").fill(account.username)
  await page
    .getByTestId("password-input")
    .fill((await reset.json()).initial_password)
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await page
    .getByLabel("当前密码", { exact: true })
    .fill((await reset.json()).initial_password)
  await page
    .getByLabel("新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page
    .getByLabel("确认新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page.getByRole("button", { name: "保存密码" }).click()
  await expect(
    page.getByRole("heading", { name: "待你过目：系统起草的初始画像" }),
  ).toBeVisible()
  await expect(
    page.getByRole("region", { name: /^草稿改动：/ }).first(),
  ).toBeVisible()
})

test("progress shows pause, exclusions and failures; retries and confirmation use the right scope", async ({
  page,
}) => {
  // A saved pending row must not block restarting after its worker has stopped.
  let counts = { generated: 2, failed: 1, excluded: 3, pending: 1 }
  await page.route("**/initial-profiles/progress", (route) =>
    route.fulfill({
      json: {
        mode: "model",
        counts,
        failed: [
          {
            employee_id: "synthetic",
            display_name: "合成员工",
            error: "起草失败，请重试",
          },
        ],
        requests: { pending_owner: counts.generated, handed_over: 0 },
        paused_until: "2026-11-04T10:40:00Z",
        pause_reason: "ChatGPT 订阅 5 小时用量已到上限",
      },
    }),
  )
  await page.route("**/sync-runs?*", (route) =>
    route.fulfill({
      json: {
        count: 1,
        data: [
          {
            id: "synthetic-run",
            kind: "csv",
            filename: "合成工作记录.csv",
            status: "failed",
            attempts: 2,
            counts: {
              records: 10,
              evidence_added: 8,
              evidence_superseded: 1,
              evidence_retracted: 1,
              people_unlinked: 2,
            },
            error: "同步失败，请检查数据和配置后重试",
            created_at: "2026-11-03T10:00:00Z",
            finished_at: null,
          },
        ],
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/progress")
  await expect(page.getByRole("alert")).toContainText(
    "ChatGPT 订阅 5 小时用量已到上限",
  )
  const resumeAt = await page.evaluate(() =>
    new Date("2026-11-04T10:40:00Z").toLocaleString("zh-CN"),
  )
  await expect(page.getByRole("alert")).toContainText(
    `预计 ${resumeAt} 自动继续`,
  )
  await expect(
    page.getByText("3 名员工不自动起草", { exact: false }),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: "自动起草设置", exact: true }).last(),
  ).toHaveAttribute("href", "/admin/drafting")
  await expect(page.getByText("合成员工：起草失败，请重试")).toBeVisible()
  await expect(page.getByText("合成工作记录.csv")).toBeVisible()
  await expect(page.getByText("新增证据：8", { exact: true })).toBeVisible()
  await expectChineseCopy(page, ["ChatGPT"])
  await page.route("**/initial-profiles/draft", (route) =>
    route.fulfill({ status: 202, json: { queued: 1 } }),
  )
  const retry = page.waitForRequest("**/initial-profiles/draft")
  await page.getByRole("button", { name: "重试失败的", exact: true }).click()
  expect((await retry).postDataJSON()).toEqual({ scope: "failed" })
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("只覆盖员工还没过目的草稿")
    await dialog.accept()
  })
  const all = page.waitForRequest("**/initial-profiles/draft")
  await page.getByRole("button", { name: "重新起草全部", exact: true }).click()
  expect((await all).postDataJSON()).toEqual({ scope: "all" })
  await page.route("**/sync-runs/synthetic-run/retry", (route) =>
    route.fulfill({ status: 409, json: { detail: "已有同步在进行" } }),
  )
  await page.getByRole("button", { name: "重试", exact: true }).click()
  await expect(
    page.getByRole("alert").filter({ hasText: "已有同步在进行" }),
  ).toBeVisible()
  counts = { generated: 3, failed: 0, excluded: 3, pending: 0 }
  await expect(
    page.getByRole("region", { name: "初始画像起草进度" }).getByRole("status"),
  ).toContainText("已为 3 名员工生成初始画像", { timeout: 15000 })
})

for (const username of [
  "manager.platform@demo.example.com",
  "employee.platform@demo.example.com",
]) {
  test(`progress denies access to ${username}`, async ({ page }) => {
    await logInUser(page, username, process.env.DEMO_USER_PASSWORD!)
    await expect(
      page.getByRole("link", { name: "同步与起草进度", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/progress")
    await expect(page.getByRole("alert")).toHaveText("需要企业管理员权限")
    await expect(
      page.getByRole("region", { name: "同步记录", exact: true }),
    ).toHaveCount(0)
  })
}

test("no model shows configuration guidance and disables drafting", async ({
  page,
}) => {
  await page.route("**/initial-profiles/progress", (route) =>
    route.fulfill({
      json: {
        mode: "none",
        counts: { failed: 1, not_started: 2 },
        failed: [],
        requests: { pending_owner: 0, handed_over: 0 },
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/progress")
  const progress = page.getByRole("region", { name: "初始画像起草进度" })
  await expect(progress.getByRole("alert")).toContainText(
    "还没有配置模型，请先在设置页配置模型，或者让员工用 JSON 导入画像",
  )
  await expect(
    progress.getByRole("link", { name: "去配置模型" }),
  ).toHaveAttribute("href", "/admin/model")
  await expect(
    progress.getByRole("button", { name: "重新起草全部" }),
  ).toBeDisabled()
  await expect(
    progress.getByRole("button", { name: "重试失败的" }),
  ).toBeDisabled()
  await expectChineseCopy(page, ["JSON"])
})
