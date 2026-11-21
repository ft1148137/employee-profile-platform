import { readFileSync } from "node:fs"
import path from "node:path"
import type {
  CapabilitiesPublic,
  ChangeRequestsPublic,
  ExternalIdentitiesPublic,
  RosterImportPreview,
  SetupPublic,
} from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { enableAutoDrafting, expect, jiraTestUrl, test } from "./utils/model"
import { addDraftingEvidence } from "./utils/sources"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("employee reviews initial profile and updates on desktop and mobile", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  test.setTimeout(120000)
  await page.setViewportSize({ width: 1440, height: 900 })
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
  const roster = readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../examples/test-enterprises/qingli-saas/roster.csv",
    ),
    "utf8",
  )
  async function applyRoster(csv: string) {
    const uploaded = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(csv),
        },
      },
    })
    await expect(uploaded).toBeOK()
    const preview: RosterImportPreview = await uploaded.json()
    const applied = await request.post(
      `${base}/roster/imports/${preview.id}/apply`,
      {
        headers,
        data: { base_revision: preview.base_revision },
      },
    )
    await expect(applied).toBeOK()
  }
  await applyRoster(roster)
  expect(
    (
      await request.post(`${base}/capability-schema`, {
        headers,
        data: { template_id: "tech-rnd" },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.put(`${base}/sources/jira`, {
        headers,
        data: {
          deployment: "cloud",
          base_url: new URL("/qingli-saas", jiraTestUrl).href,
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
    await request.get(`${base}/external-identities?status=all&limit=200`, {
      headers,
    })
  ).json()
  expect(identities.count).toBeGreaterThan(0)
  const candidates = identities.data.filter((identity) => identity.candidate)
  expect(
    identities.data.filter(
      (identity) => identity.linked_by === "system_worker:email_match",
    ).length,
  ).toBeGreaterThan(0)
  const links = candidates.map((identity) => ({
    identity_id: identity.id,
    employee_id: identity.candidate!.id,
  }))
  if (links.length) {
    const linked = await request.post(`${base}/external-identities/link`, {
      headers,
      data: { links },
    })
    expect(linked.ok()).toBeTruthy()
    expect((await linked.json()).linked).toBe(links.length)
  }
  // Keep Jira's original emails through linking, then use unique login emails.
  await applyRoster(
    roster.replace(
      /@qingli-saas\.example\.com/g,
      `+${username}@qingli-saas.example.com`,
    ),
  )
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.put(`${base}/model-setting`, {
        headers,
        data: {
          enabled: true,
          kind: "openai_compatible",
          model: "synthetic",
          provider: "openai-compatible",
          base_url: modelUrl,
          api_key: "synthetic-key",
        },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.put(`${base}/drafting-settings`, {
        headers,
        data: { enabled: true },
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
  await expect(page).toHaveURL(
    /\/assistant\?mode=admin(?:&session=[0-9a-f-]+)?$/,
  )
  await page.goto("/admin/data-ai?tab=model")
  const blocked = await request.post(
    `${base}/setup/steps/initial_profiles/complete`,
    { headers },
  )
  expect(blocked.status()).toBe(422)
  expect((await blocked.json()).detail.blockers.join(" ")).toContain(
    "名员工没有初始画像",
  )
  await addDraftingEvidence(page)
  page.once("dialog", (dialog) => dialog.accept())
  await page.getByRole("button", { name: "重新起草全部", exact: true }).click()
  const accounts = await (
    await request.get(`${base}/accounts?employment_status=active&limit=100`, {
      headers,
    })
  ).json()
  const total = accounts.count
  expect(total).toBeGreaterThan(0)
  await expect(
    page.getByText(
      `已处理 ${total} 人，已生成初稿 ${total} 人，暂无内容 0 人。`,
    ),
  ).toBeVisible({ timeout: 60000 })
  await expectChineseCopy(
    page.getByRole("region", { name: "初始画像起草进度" }),
  )
  expect(
    (
      await request.post(`${base}/setup/steps/initial_profiles/complete`, {
        headers,
      })
    ).ok(),
  ).toBeTruthy()
  await page.goto("/admin/data-ai?tab=sources")
  const syncSection = page
    .getByRole("region", {
      name: "同步记录",
      exact: true,
    })
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "Jira", exact: true }) })
  await expect(syncSection.getByText("Jira", { exact: true })).toBeVisible()
  await expect(
    syncSection.getByRole("cell", { name: "已完成 1 次", exact: true }),
  ).toBeVisible()
  await expect(syncSection.getByText("记录：38", { exact: true })).toBeVisible()
  await page.goto("/admin/data-ai?tab=model")
  await expect(
    page.getByRole("region", { name: "初始画像起草进度" }),
  ).toContainText(`已生成初稿 ${total} 人`)
  await page.screenshot({
    path: testInfo.outputPath("initial-profiles-progress.png"),
    fullPage: true,
  })
  const employee = identities.data
    .map((identity) => identity.employee ?? identity.candidate)
    .find((employee) => employee?.employee_code === "QL003")!
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
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.locator("summary", { hasText: /待你确认：初始画像草稿/ }).click()
  await expect(
    page.getByRole("heading", { name: "待你确认：初始画像草稿" }),
  ).toBeVisible()
  const draftItem = page.getByRole("region", { name: /^草稿改动：/ }).first()
  await expect(draftItem).toBeVisible()
  await expect(draftItem).toContainText("合成模型能力建议")
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole("button", { name: "直接提交", exact: true }).click()
    await expect(
      page.getByRole("dialog").getByRole("heading", { name: "变更预览" }),
    ).toBeVisible()
    await expect(
      page
        .getByRole("dialog")
        .getByRole("button", { name: "确认提交", exact: true }),
    ).toBeEnabled()
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "关闭", exact: true })
      .click()
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    const employeeHeaders = {
      Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("access_token"))}`,
    }
    const requests: ChangeRequestsPublic = await (
      await request.get(`${base}/me/requests`, { headers: employeeHeaders })
    ).json()
    const initial = requests.data.find((r) => r.origin === "initial_sync")
    const dictionary: CapabilitiesPublic = await (
      await request.get(`${base}/me/capabilities`, { headers: employeeHeaders })
    ).json()
    const newCapabilities = dictionary.data.filter(
      (c) => !initial?.items?.some((i) => i.capability.key === c.key),
    )
    const newCapability = newCapabilities[0]
    if (initial) {
      await page
        .getByRole("region", { name: "初始画像草稿", exact: true })
        .getByRole("button", { name: "提异议", exact: true })
        .first()
        .click()
    } else {
      await page.getByRole("button", { name: "添加能力", exact: true }).click()
      await page
        .getByRole("dialog")
        .getByLabel("搜索能力")
        .fill(newCapability.name)
      await page
        .getByRole("dialog")
        .getByRole("button", { name: newCapability.name, exact: true })
        .click()
      await page.getByRole("button", { name: "手动编辑", exact: true }).click()
    }
    let editor = page.getByRole("dialog")
    if (initial)
      await editor
        .getByLabel("异议原因")
        .first()
        .fill("系统判断的工作范围需要本人补充")
    if (initial) {
      await editor.getByLabel("新增能力").selectOption(newCapabilities[1].key)
      await editor
        .getByRole("textbox", { name: "能力说明" })
        .last()
        .fill("补充另一项合成能力")
      await editor.getByLabel("新增能力").selectOption(newCapability.key)
    }
    await editor
      .getByRole("textbox", { name: "能力说明", exact: true })
      .last()
      .fill("我补充了一段合成项目经历")
    await editor.getByLabel("补充说明").last().fill("本人完成了合成项目的验证")
    await expect(editor.getByRole("textbox", { name: "能力说明" })).toHaveCount(
      initial ? 3 : 1,
    )
    const saveDraft = editor.getByRole("button", {
      name: "保存草稿",
      exact: true,
    })
    const previewDraft = editor.getByRole("button", {
      name: "预览并提交",
      exact: true,
    })
    await expect(saveDraft).toBeInViewport({ ratio: 1 })
    await expect(previewDraft).toBeInViewport({ ratio: 1 })
    await saveDraft.click()
    await expect(editor.getByRole("status")).toHaveText("草稿已保存")
    await editor.getByRole("button", { name: "关闭", exact: true }).click()
    await page.locator("summary", { hasText: /^其他待处理请求/ }).click()
    await page.getByRole("button", { name: "继续修改", exact: true }).click()
    editor = page.getByRole("dialog")
    await expect(
      editor.getByRole("textbox", { name: "能力说明", exact: true }).last(),
    ).toHaveValue("我补充了一段合成项目经历")
    await expect(saveDraft).toBeInViewport({ ratio: 1 })
    await expect(previewDraft).toBeInViewport({ ratio: 1 })
    await previewDraft.click()
    await expect(
      editor.getByRole("heading", { name: "变更预览" }),
    ).toBeVisible()
    await expect(editor).toContainText("本人完成了合成项目的验证")
    for (const name of ["返回修改", "确认提交"]) {
      await expect(editor.getByRole("button", { name })).toBeInViewport({
        ratio: 1,
      })
    }
    await page.screenshot({
      path: testInfo.outputPath(`profile-form-preview-${width}.png`),
      fullPage: true,
    })
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true)
    // Another real tab edits the saved request after this tab has previewed it.
    const other = await page.context().newPage()
    await other.goto(page.url())
    await other.locator("summary", { hasText: /^其他待处理请求/ }).click()
    await other.getByRole("button", { name: "继续修改", exact: true }).click()
    await other
      .getByRole("dialog")
      .getByRole("textbox", { name: "能力说明", exact: true })
      .last()
      .fill("另一标签页补充的最新经历")
    await other.getByRole("button", { name: "保存草稿", exact: true }).click()
    await expect(other.getByRole("status")).toHaveText("草稿已保存")
    await other.close()
    await editor.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(editor).toContainText("内容已更新，请重新预览")
    await editor
      .getByRole("button", { name: "重新预览最新内容", exact: true })
      .click()
    await expect(editor).toContainText("另一标签页补充的最新经历")
    await editor.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(editor).toHaveCount(0)
    await expect(
      page.getByRole("region", { name: "其他待处理请求" }),
    ).toContainText("待经理审批")
    const submitted: ChangeRequestsPublic = await (
      await request.get(`${base}/me/requests`, { headers: employeeHeaders })
    ).json()
    const sent = submitted.data[0]
    expect(sent.status).toBe("pending_review")
    expect(sent.submitted_via).toBe("web")
    if (initial) expect(sent.id).toBe(initial.id)
    page.once("dialog", async (dialog) => {
      await dialog.accept()
    })
    await page.getByRole("button", { name: "撤回", exact: true }).click()
    await expect(
      page.getByRole("region", { name: "其他待处理请求" }),
    ).toHaveCount(0)
    const history: ChangeRequestsPublic = await (
      await request.get(`${base}/me/requests?status=all`, {
        headers: employeeHeaders,
      })
    ).json()
    expect(history.data.find((r) => r.id === sent.id)?.status).toBe("withdrawn")
  }
})

test("the fake model accepts department defaults without drafting records", async ({
  modelUrl,
  request,
}) => {
  // Playwright owns the server, even when Pi connects through a Docker host alias.
  const endpoint = new URL(`${modelUrl}/chat/completions`)
  endpoint.hostname = "127.0.0.1"
  const response = await request.post(endpoint.href, {
    data: {
      model: "synthetic",
      stream: true,
      tools: [
        {
          type: "function",
          function: { name: "submit_department_capabilities" },
        },
      ],
      messages: [
        {
          role: "user",
          content: JSON.stringify({
            department_name: "合成部门",
            capabilities: [{ key: "synthetic" }],
          }),
        },
      ],
    },
  })
  await expect(response).toBeOK()
  const [event] = (await response.text()).split("\n\n")
  const { function: call } = JSON.parse(event.slice("data: ".length)).choices[0]
    .delta.tool_calls[0]
  expect(call.name).toBe("submit_department_capabilities")
  expect(JSON.parse(call.arguments)).toEqual({ capability_keys: [] })
})

test("progress shows pause, exclusions and failures; retries and confirmation use the right scope", async ({
  page,
}) => {
  // A saved pending row must not block restarting after its worker has stopped.
  let counts = {
    generated: 2,
    processed: 3,
    drafted: 2,
    no_content: 0,
    failed: 1,
    excluded: 3,
    pending: 1,
  }
  const modelErrors = [
    "这个模型不可用，请换一个模型",
    "模型服务拒绝了请求，请检查模型设置",
    "模型服务暂时不可用，请稍后重试",
  ]
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
          ...modelErrors.map((error, index) => ({
            employee_id: `synthetic-${index}`,
            display_name: `合成员工${index}`,
            error,
          })),
        ],
        requests: { pending_owner: counts.drafted, handed_over: 0 },
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
  await page.goto("/admin/data-ai?tab=model")
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
    page.getByText("3 名员工本人选择不参与自动起草", { exact: false }),
  ).toBeVisible()
  await expect(
    page.getByText("可由员工在个人设置里修改", { exact: false }),
  ).toBeVisible()
  await expect(page.getByText("合成员工：起草失败，请重试")).toBeVisible()
  for (const error of modelErrors) {
    await expect(page.getByText(error, { exact: false })).toBeVisible()
  }
  await expectChineseCopy(
    page.getByRole("region", { name: "初始画像起草进度" }),
    ["ChatGPT"],
  )
  await page.goto("/admin/data-ai?tab=sources")
  const syncRecords = page.getByRole("region", { name: "同步记录" })
  await expect(syncRecords.getByText("合成工作记录.csv")).toBeVisible()
  await expect(
    syncRecords.getByText("新增证据：8", { exact: true }),
  ).toBeVisible()
  await expectChineseCopy(syncRecords)
  await enableAutoDrafting(page)
  await page.goto("/admin/data-ai?tab=model")
  await page.route("**/initial-profiles/draft", (route) =>
    route.fulfill({ status: 202, json: { queued: 1 } }),
  )
  const retry = page.waitForRequest("**/initial-profiles/draft")
  await page.getByRole("button", { name: "重试失败项", exact: true }).click()
  expect((await retry).postDataJSON()).toEqual({ scope: "failed" })
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("将仅覆盖员工尚未确认的草稿")
    await dialog.accept()
  })
  const all = page.waitForRequest("**/initial-profiles/draft")
  await page.getByRole("button", { name: "重新起草全部", exact: true }).click()
  expect((await all).postDataJSON()).toEqual({ scope: "all" })
  await page.route("**/sync-runs/synthetic-run/retry", (route) =>
    route.fulfill({ status: 409, json: { detail: "已有同步在进行" } }),
  )
  await page.goto("/admin/data-ai?tab=sources")
  await page.getByRole("button", { name: "重试", exact: true }).click()
  await expect(
    page.getByRole("alert").filter({ hasText: "已有同步在进行" }),
  ).toBeVisible()
  counts = {
    generated: 50,
    processed: 50,
    drafted: 3,
    no_content: 47,
    failed: 0,
    excluded: 3,
    pending: 0,
  }
  await page.goto("/admin/data-ai?tab=model")
  const progress = page.getByRole("region", { name: "初始画像起草进度" })
  const bar = progress.getByRole("progressbar", { name: "已处理" })
  await expect(bar).toHaveAttribute("value", "50")
  await expect(bar).toHaveAttribute("max", "50")
  for (const [label, count] of [
    ["已处理", "50"],
    ["已生成初稿", "3"],
    ["暂无内容", "47"],
  ]) {
    const term = progress
      .getByRole("term")
      .and(progress.getByText(label, { exact: true }))
    await expect(term.locator("..").getByRole("definition")).toHaveText(count)
  }
  await expect(progress.getByRole("status")).toHaveText(
    "已处理 50 人，已生成初稿 3 人，暂无内容 47 人。",
  )
  await expect(
    progress.getByText(
      "暂无内容包括没有可用的外部工作记录，以及已有记录但未形成可用判断。",
    ),
  ).toBeVisible()
})

for (const username of [
  "manager.platform@demo.example.com",
  "employee.platform@demo.example.com",
]) {
  test(`progress denies access to ${username}`, async ({ page }) => {
    await logInUser(page, username, process.env.DEMO_USER_PASSWORD!)
    await expect(
      page.getByRole("link", { name: "首页", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/data-ai?tab=model")
    await expect(page.getByRole("alert")).toHaveText("需要企业管理员权限")
    await expect(
      page.getByRole("region", { name: "初始画像起草进度", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/data-ai?tab=sources")
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
  await page.goto("/admin/data-ai?tab=model")
  const progress = page.getByRole("region", { name: "初始画像起草进度" })
  await expect(progress.getByRole("alert")).toContainText(
    "尚未配置 AI 模型，请先前往「设置 → 模型」配置，或由员工手动/导入维护画像。",
  )
  await expect(
    progress.getByRole("link", { name: "去配置模型" }),
  ).toHaveAttribute("href", "/admin/model")
  await expect(
    progress.getByRole("button", { name: "重新起草全部" }),
  ).toBeDisabled()
  await expect(
    progress.getByRole("button", { name: "重试失败项" }),
  ).toBeDisabled()
  await expectChineseCopy(progress, ["JSON"])
})

test("administrator previews and confirms overdue initial handover", async ({
  page,
}) => {
  let count = 2
  await page.route("**/initial-profiles/hand-over", (route) => {
    if (route.request().method() === "POST") {
      count = 0
      return route.fulfill({ json: { count: 2 } })
    }
    return route.fulfill({ json: { count } })
  })
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/data-ai?tab=model")
  await expect(page.getByText("满 14 天未处理、可批量送审：2 人")).toBeVisible()
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("2 名员工")
    await dialog.accept()
  })
  const posted = page.waitForRequest(
    (r) => r.url().endsWith("/hand-over") && r.method() === "POST",
  )
  await page.getByRole("button", { name: "批量送审", exact: true }).click()
  await posted
  await expect(page.getByText("已送审 2 人", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "批量送审", exact: true }),
  ).toBeDisabled()
})
