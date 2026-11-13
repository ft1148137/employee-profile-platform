import type { APIRequestContext } from "@playwright/test"
import {
  mockAdminMessages,
  openAdminAssistant,
  returnToManualPage,
} from "./utils/adminAssistant"
import { expectChineseCopy } from "./utils/copy"
import { expect, jiraTestUrl, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

async function createOrg(request: APIRequestContext, modelUrl: string) {
  const email = `admin-change-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "提醒设置合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
  expect(
    (
      await request.put(`${base}/model-setting`, {
        headers,
        data: {
          enabled: true,
          kind: "openai_compatible",
          provider: "openai-compatible",
          model: "synthetic",
          base_url: modelUrl,
          api_key: "synthetic-key",
        },
      })
    ).ok(),
  ).toBeTruthy()
  return { email, password, base, headers }
}

test("reset password requires two clicks and disappears after reload", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,reset-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
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
  const { data: people } = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill(`重置密码 ${people[0].account.user_id}`)
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card.getByText("重置密码", { exact: true })).toBeVisible({
    timeout: 30000,
  })
  await expect(card.getByText(/涉及账号：合成员工/)).toBeVisible()
  await expect(card.getByText("修改前", { exact: true })).toHaveCount(0)
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveCount(0)
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/apply") &&
      response.request().method() === "POST",
  )
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  const response = await responsePromise
  expect(response.headers()["cache-control"]).toBe("no-store")
  const secret = (await response.json()).one_time[0].initial_password
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveValue(secret)
  await expect(card.getByRole("button", { name: "复制初始密码" })).toBeVisible()
  await expect(
    card.getByRole("button", { name: "撤销", exact: true }),
  ).toHaveCount(0)
  await expect(card).not.toContainText(people[0].account.user_id)
  await expectChineseCopy(page, [people[0].account.user_id])
  await page.reload()
  await expect(card.getByRole("status")).toHaveText("已应用")
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveCount(0)
  await expect(page.getByText(secret, { exact: true })).toHaveCount(0)
})

test("Jira draft accepts a private token, tests it and highlights the source page", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/data-ai?tab=sources")
  await openAdminAssistant(page)
  const dialog = page.getByRole("region", { name: "管理助手", exact: true })
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("修改 Jira 站点")
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  const card = dialog.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toBeVisible({ timeout: 30000 })
  const secure = dialog.getByRole("form", { name: "安全输入框" })
  await secure.locator('input[type="password"]').fill("mock-cloud-token")
  await secure.getByRole("button", { name: "安全提交" }).click()
  await expect(secure.getByRole("status")).toHaveText("已填写")
  await card.getByRole("button", { name: "测试连接" }).click()
  await expect(card.getByText("测试连接：成功", { exact: true })).toBeVisible({
    timeout: 30000,
  })
  expect(
    (await (await request.get(`${base}/sources/jira`, { headers })).json())
      .configured,
  ).toBe(false)
  await returnToManualPage(page)
  const section = page.getByRole("region", { name: "Jira 连接" })
  await expect(section.getByLabel("站点地址")).toHaveValue(jiraTestUrl)
  await expect(
    section.locator('[data-assistant-suggested="true"]').first(),
  ).toBeVisible()
  await expect(
    section.getByRole("button", { name: "保存", exact: true }),
  ).toBeDisabled()
  await section.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(
    section.getByRole("region", { name: "页面草稿确认" }),
  ).toHaveCount(0)
  expect(
    (await (await request.get(`${base}/sources/jira`, { headers })).json())
      .configured,
  ).toBe(true)
  await expectChineseCopy(page, [
    "Cloud",
    "Data Center",
    "Server",
    "token",
    "description",
    "Atlassian",
    "PLM",
    "Git",
  ])
})

for (const width of [1440, 390]) {
  test(`reminder change is previewed, applied and undone at ${width}px`, async ({
    page,
    request,
    modelUrl,
  }, testInfo) => {
    test.setTimeout(90000)
    const { email, password, base, headers } = await createOrg(
      request,
      modelUrl,
    )
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(page, email, password)
    await page.setViewportSize({ width, height: 900 })
    await page.goto("/admin/rules?tab=reminders")
    await openAdminAssistant(page)
    const dialog = page.getByRole("region", { name: "管理助手", exact: true })
    await dialog
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await dialog.getByRole("button", { name: "发送", exact: true }).click()
    const card = dialog.getByRole("region", { name: "设置改动确认卡" })
    await expect(card).toBeVisible({ timeout: 30000 })
    await expect(card.getByText("每季度", { exact: true })).toBeVisible()
    await expect(card.getByText("每年", { exact: true })).toBeVisible()
    await expect(card.getByText("36 个月", { exact: true })).toBeVisible()
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await card.getByRole("button", { name: "确认应用" }).click()
    await expect(card.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("yearly")
    await page.screenshot({
      path: testInfo.outputPath(`change-applied-${width}.png`),
    })
    await card.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(card.getByRole("status")).toHaveText("已撤销")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await expectChineseCopy(page)
    await page.screenshot({
      path: testInfo.outputPath(`change-undone-${width}.png`),
    })
    await page.reload()
    await openAdminAssistant(page)
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")
    await page.reload()
    await expect(page).toHaveURL(/\/admin\/assistant\?session=/)
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")

    await page
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const latest = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await latest.getByRole("button", { name: "确认应用" }).click()
    await expect(latest.getByRole("status")).toHaveText("已应用")
    expect(
      (
        await request.put(`${base}/update-settings`, {
          headers,
          data: { reminder_schedule: "off", stale_after_months: 24 },
        })
      ).ok(),
    ).toBeTruthy()
    await latest.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await page.reload()
    const reverse = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await expect(
      reverse.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await expect(reverse.getByRole("status")).toHaveText("待确认")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("off")
    await reverse.getByRole("button", { name: "确认应用" }).click()
    await expect(reverse.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
  })
}

test("auto drafting switch shows affected people and can be undone", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,synthetic@example.com\n",
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const preview = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("打开自动起草")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card.getByText(/影响 1 人/)).toBeVisible({ timeout: 30000 })
  const read = async () =>
    (await (await request.get(`${base}/drafting-settings`, { headers })).json())
      .enabled
  expect(await read()).toBe(false)
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(await read()).toBe(true)
  await card.getByRole("button", { name: "撤销", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已撤销")
  expect(await read()).toBe(false)
})

test("closing manager confirmation requires two clicks with impact", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("关闭经理确认")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toBeVisible({ timeout: 30000 })
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  await expect(card.getByRole("alert")).toContainText("影响全体在职员工")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(true)
  await page.reload()
  await expect(
    card.getByRole("button", { name: "再次确认", exact: true }),
  ).toBeVisible()
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(false)
  await expectChineseCopy(page)
})

test("Feishu draft keeps credentials private and requires a second confirmation", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/data-ai?tab=im")
  await openAdminAssistant(page)
  const dialog = page.getByRole("region", { name: "管理助手", exact: true })
  await dialog
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("连接飞书")
  await dialog.getByRole("button", { name: "发送", exact: true }).click()
  const secure = dialog.getByRole("form", { name: "安全输入框" })
  await expect(secure).toHaveCount(2, { timeout: 30000 })
  for (const [index, value] of [
    "synthetic-feishu-id",
    "sk-e2e-feishu-canary-283",
  ].entries()) {
    await secure.nth(index).locator('input[type="password"]').fill(value)
    await secure.nth(index).getByRole("button", { name: "安全提交" }).click()
    await expect(secure.nth(index).getByRole("status")).toHaveText("已填写")
  }
  // The backend tests exercise the adapter; this spec isolates confirmation UI from Feishu.
  await page.route("**/change-sets/*/test", (route) =>
    route.fulfill({ json: { ok: true, message: "连接成功" } }),
  )
  const card = dialog.getByRole("region", { name: "设置改动确认卡" })
  await card.getByRole("button", { name: "测试连接" }).click()
  await expect(card.getByText("测试连接：成功", { exact: true })).toBeVisible()
  await returnToManualPage(page)
  const form = page.getByRole("form", { name: "飞书", exact: true })
  await expect(form.getByRole("checkbox", { name: "启用入口" })).toBeChecked()
  await expect(form.locator('[data-assistant-suggested="true"]')).toHaveCount(2)
  await expect(
    form.getByRole("button", { name: "保存", exact: true }),
  ).toBeDisabled()
  const read = async () =>
    (await (await request.get(`${base}/im-settings`, { headers })).json()).find(
      (row: { platform: string }) => row.platform === "feishu",
    )
  const confirmation = page.getByRole("region", { name: "页面草稿确认" })
  await confirmation.getByRole("button", { name: "确认应用" }).click()
  await expect(confirmation.getByRole("status")).toHaveText("请再次确认")
  expect((await read()).enabled).toBe(false)
  await expect(confirmation.getByRole("alert")).toContainText(
    "消息会存在该平台的服务器上",
  )
  await confirmation
    .getByRole("button", { name: "再次确认", exact: true })
    .click()
  await expect(confirmation).toHaveCount(0)
  expect((await read()).enabled).toBe(true)
  await expect(form.getByLabel("应用密钥", { exact: true })).toHaveValue("")
  await expectChineseCopy(page, ["IM", "Telegram"])
})

test("saving credentials and retesting clear the previous apply error", async ({
  page,
}) => {
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  const id = crypto.randomUUID()
  const draft = {
    id,
    revision: 1,
    status: "draft",
    undo_of_id: null,
    items: [
      {
        function: "update_im_setting",
        title: "修改飞书入口",
        args: {},
        before: { enabled: false },
        after: { enabled: true },
        risk: "high",
        testable: true,
      },
    ],
  }
  const saveReply = await mockAdminMessages(page)
  await page.route("**/admin-assistant/sessions/*/turns?*", (route) => {
    const reply = {
      messages: [
        { type: "admin_change_set", change_set: draft },
        {
          type: "secret_input",
          change_set_id: id,
          field: "app_secret",
          title: "安全填写密钥",
        },
      ],
    }
    saveReply(route.request().url(), reply)
    return route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: reply })}\n`,
    })
  })
  await page.route(`**/change-sets/${id}`, (route) =>
    route.fulfill({ json: draft }),
  )
  await page.route(`**/change-sets/${id}/apply`, (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "还缺少应用密钥，请先填写" },
    }),
  )
  await page.route(`**/change-sets/${id}/secrets/app_secret`, (route) =>
    route.fulfill({ json: {} }),
  )
  await page.route(`**/change-sets/${id}/test`, (route) =>
    route.fulfill({
      json: {
        ok: false,
        message: "应用凭据无效或没有权限。请到飞书开放平台核对。",
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("连接飞书")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  const oldError = card.getByText("还缺少应用密钥，请先填写", { exact: true })
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(oldError).toBeVisible()
  const secure = page.getByRole("form", { name: "安全输入框" })
  await secure.locator('input[type="password"]').fill("synthetic-new-key")
  await secure.getByRole("button", { name: "安全提交" }).click()
  await expect(secure.getByRole("status")).toHaveText("已填写")
  await expect(oldError).toHaveCount(0)
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(oldError).toBeVisible()
  await card.getByRole("button", { name: "测试连接" }).click()
  await expect(
    card.getByText(/测试连接：失败，应用凭据无效或没有权限/),
  ).toBeVisible()
  await expect(oldError).toHaveCount(0)
})

for (const functionName of [
  "update_capability_dictionary",
  "apply_capability_template",
  "draft_dictionary_from_material",
]) {
  test(`${functionName} shows readable capability changes`, async ({
    page,
  }) => {
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({ json: { available: true, reason: "" } }),
    )
    const definition = {
      key: "synthetic_skill",
      name: "合成需求分析",
      category: "产品研发",
      description: "合成能力说明，默认收起",
      level_rubric: {
        levels: [{ key: "l1", label: "熟练处理", description: "可以独立完成" }],
      },
      changed_fields: [],
    }
    const id = crypto.randomUUID()
    const draft = {
      id,
      revision: 1,
      status: "draft",
      undo_of_id: null,
      items: [
        {
          function: functionName,
          title: "更新能力词典",
          args: {},
          before: { definitions: [] },
          after: { capabilities: [definition] },
          risk: "normal",
          undoable: true,
          impact: {
            count: 2,
            text: "模板「合成模板」，2 项能力；会替换当前词典，可以撤销",
            dictionary: {
              added: [
                definition,
                {
                  ...definition,
                  key: "synthetic_delivery",
                  name: "合成交付协作",
                },
              ],
              modified: [
                {
                  ...definition,
                  key: "synthetic_modified",
                  name: "合成修改能力",
                  changed_fields: ["名称", "等级"],
                },
              ],
              removed: [
                {
                  ...definition,
                  key: "synthetic_removed",
                  name: "合成删除能力",
                },
              ],
            },
          },
        },
      ],
    }
    const saveReply = await mockAdminMessages(page)
    await page.route("**/admin-assistant/sessions/*/turns?*", (route) => {
      const reply = {
        messages: [{ type: "admin_change_set", change_set: draft }],
      }
      saveReply(route.request().url(), reply)
      return route.fulfill({
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ type: "final", response: reply })}\n`,
      })
    })
    await page.route(`**/change-sets/${id}`, (route) =>
      route.fulfill({ json: draft }),
    )
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.goto("/admin/assistant")
    await page
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("整理能力词典")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const card = page.getByRole("region", { name: "设置改动确认卡" })
    await expect(card.getByText("新增 2 项", { exact: true })).toBeVisible()
    await expect(card.getByText("修改 1 项", { exact: true })).toBeVisible()
    await expect(card.getByText("删除 1 项", { exact: true })).toBeVisible()
    await expect(card.getByText("合成需求分析", { exact: true })).toBeVisible()
    await expect(card.getByText("合成交付协作", { exact: true })).toBeVisible()
    await expect(
      card.getByText("修改了：名称、等级", { exact: true }),
    ).toBeVisible()
    await expect(
      card.getByText("分类：产品研发", { exact: true }).first(),
    ).toBeVisible()
    await expect(
      card.getByText("等级：熟练处理", { exact: true }).first(),
    ).toBeVisible()
    await expect(card).not.toContainText('{"')
    const description = card
      .getByText("合成能力说明，默认收起", { exact: true })
      .first()
    await expect(description).not.toBeVisible()
    await card.getByText("查看说明", { exact: true }).first().click()
    await expect(description).toBeVisible()
  })
}
