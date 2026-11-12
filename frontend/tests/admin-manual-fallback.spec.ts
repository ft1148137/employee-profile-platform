import { readFile } from "node:fs/promises"
import path from "node:path"
import { expect, test } from "@playwright/test"
import type { AccountsPublic, ExternalIdentitiesPublic } from "../src/client"
import { importRoster } from "./utils/roster"
import { saveCloud } from "./utils/sources"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

// One isolated enterprise per run, including retries; shared demo settings stay intact.
test("manual console completes all fifteen tabs without a model", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(240000)
  const suffix = crypto.randomUUID()
  const email = `manual-${suffix}@example.com`
  const employeeEmail = (code: string) =>
    `${code}+${suffix}@qiguang-aiot.example.com`
  const password = "synthetic-password-2026"
  const setupToken = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(setupToken.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setupToken.json()).token,
      org_name: "启光智联手工兜底验证",
      admin_username: email,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id, access_token } = await initialized.json()
  const base = `/api/v1/orgs/${org_id}`
  const headers = { Authorization: `Bearer ${access_token}` }
  const get = async (url: string) => {
    const response = await request.get(`${base}${url}`, { headers })
    expect(response.ok(), `${url}: ${response.status()}`).toBeTruthy()
    return response.json()
  }
  const post = async (url: string, data = {}, auth = headers) => {
    const response = await request.post(`${base}${url}`, {
      headers: auth,
      data,
    })
    expect(response.ok(), `${url}: ${response.status()}`).toBeTruthy()
    return response.json()
  }
  const sample = (name: string) =>
    path.resolve(
      import.meta.dirname,
      "../../examples/test-enterprises/qiguang-aiot",
      name,
    )
  const screenshot = async (name: string) =>
    page.screenshot({
      path: testInfo.outputPath(`${name}.png`),
      fullPage: true,
    })
  let turns = 0
  page.on("request", (req) => {
    if (req.url().includes("/turns?")) turns++
  })

  await test.step("unconfigured model gives the real manual landing page", async () => {
    expect(await get("/model-setting")).toMatchObject({
      enabled: false,
      has_api_key: false,
    })
    await post("/setup/steps/model/skip")
    await logInUser(page, email, password)
    await expect(page).toHaveURL(/\/admin\/?$/)
    await expect(
      page.getByText("请先在模型设置里配置模型，也可以继续使用手工页面。", {
        exact: true,
      }),
    ).toBeVisible()
    const availability = await request.get(
      `/api/v1/admin-assistant/availability?org_id=${org_id}`,
      { headers },
    )
    expect(availability.ok()).toBeTruthy()
    expect(await availability.json()).toMatchObject({ available: false })
    await expect(
      page.getByRole("button", { name: "重新起草全部", exact: true }),
    ).toBeDisabled()
    await screenshot("home")
  })

  await test.step("prepare the synthetic roster through the retained API", async () => {
    await importRoster(
      page,
      Buffer.from(
        (await readFile(sample("roster.csv"), "utf8")).replace(
          /@qiguang-aiot\.example\.com/g,
          `+${suffix}@qiguang-aiot.example.com`,
        ),
      ),
    )
    await page.goto("/admin/people?tab=accounts")
    expect((await get("/accounts")).count).toBeGreaterThan(0)
  })

  await test.step("people / accounts: issue accounts and retain the result", async () => {
    await page.getByRole("button", { name: "生成账号", exact: true }).click()
    const dialog = page.getByRole("dialog")
    const download = page.waitForEvent("download")
    await dialog.getByRole("button", { name: "确认生成并下载" }).click()
    expect(await (await download).failure()).toBeNull()
    await expect(dialog.getByRole("status")).toContainText(
      /已生成 [1-9]\d* 个账号/,
    )
    await dialog
      .getByRole("button", { name: "关闭", exact: true })
      .first()
      .click()
    await page.reload()
    const accounts: AccountsPublic = await get("/accounts")
    expect(
      accounts.data.every((employee) => employee.account !== null),
    ).toBeTruthy()
    await expect(
      page.getByRole("row").filter({ hasText: "QG002" }),
    ).toContainText("待首次登录")
    await screenshot("people")
  })

  await test.step("people / roles: grant a department manager", async () => {
    const employee = (await get("/accounts?q=QG002")).data[0]
    await page.goto(`/employees/${employee.id}?tab=account`)
    await page.getByRole("button", { name: "授予角色", exact: true }).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("textbox", { name: "搜索姓名或工号" }).fill("QG002")
    await dialog.getByRole("combobox", { name: "账号", exact: true }).click()
    await page.getByRole("option", { name: /qg002/i }).click()
    await dialog.getByRole("combobox", { name: "部门", exact: true }).click()
    await page.getByRole("option", { name: "硬件研发部", exact: true }).click()
    await dialog.getByRole("button", { name: "确认授予", exact: true }).click()
    await expect(dialog.getByRole("status")).toContainText("角色已授予")
    await dialog
      .getByRole("button", { name: "关闭", exact: true })
      .first()
      .click()
    await page.reload()
    const manager = page
      .getByRole("row")
      .filter({ hasText: employeeEmail("qg002") })
    await expect(manager).toContainText("部门经理")
    await manager.getByRole("button", { name: "撤销角色", exact: true }).click()
    await dialog
      .getByRole("button", { name: "确认撤销角色", exact: true })
      .click()
    await expect(manager).toHaveCount(0)
  })

  await test.step("rules / dictionary: publish an imported dictionary", async () => {
    await page.getByRole("link", { name: "能力与规则", exact: true }).click()
    await page.getByRole("button", { name: "编辑词典", exact: true }).click()
    await page
      .getByLabel("上传 YAML 或 Excel 词典（不超过 2 MB）")
      .setInputFiles({
        name: "dictionary.yaml",
        mimeType: "application/yaml",
        buffer: Buffer.from(
          "- key: hardware\n  name: 硬件设计\n  category: 工程\n  description: 合成硬件设计能力\n  level_rubric:\n    levels:\n      - key: basic\n        label: 基础\n        description: 在指导下完成\n",
        ),
      })
    await expect(
      page.getByRole("region", { name: "词典改动预览" }),
    ).toContainText("新增 1 项")
    await page.getByRole("button", { name: "确认保存新版本" }).click()
    await expect(
      page.getByRole("status").filter({ hasText: "新词典已启用" }),
    ).toBeVisible()
    await page.reload()
    await expect(page.getByText("当前第 1 版 · 1 项能力")).toBeVisible()
    await screenshot("rules")
  })

  await test.step("rules / approval: save and reopen", async () => {
    await page.getByRole("tab", { name: "审批规则", exact: true }).click()
    const form = page.getByRole("form", { name: "审批规则", exact: true })
    await form.getByLabel("员工过目期限（天）").fill("7")
    await form.getByRole("button", { name: "保存审批规则" }).click()
    await expect(form.getByRole("status")).toContainText(
      "审批规则已保存为新版本。",
    )
    await page.reload()
    await expect(form.getByLabel("员工过目期限（天）")).toHaveValue("7")
  })

  await test.step("model / drafting: disabled without a model", async () => {
    await page.goto("/admin/data-ai?tab=model")
    const card = page.getByRole("region", { name: "自动起草", exact: true })
    await expect(card.getByRole("switch")).not.toBeChecked()
    await expect(card.getByRole("switch")).toBeDisabled()
    await expect(card).toContainText("模型未启用")
    await screenshot("drafting-off")
    await page.goto("/admin/rules?tab=reminders")
  })

  await test.step("rules / reminders: save a yearly reminder", async () => {
    await page.getByRole("tab", { name: "定期提醒", exact: true }).click()
    const section = page.getByRole("region", { name: "持续更新提醒" })
    await section.getByRole("combobox", { name: "提醒周期" }).click()
    await page.getByRole("option", { name: "每年", exact: true }).click()
    await section.getByLabel("证据较旧期限（月）").fill("36")
    await section.getByRole("button", { name: "保存提醒设置" }).click()
    await expect(section.getByRole("status")).toContainText("已保存")
    await page.reload()
    await expect(section.getByRole("combobox")).toHaveText("每年")
    await expect(section.getByLabel("证据较旧期限（月）")).toHaveValue("36")
  })

  await test.step("data / sources: save Jira and import work records", async () => {
    await page.getByRole("link", { name: "数据与 AI", exact: true }).click()
    await saveCloud(
      page,
      "https://synthetic.atlassian.net",
      "jira-admin@example.com",
      "synthetic-jira-key",
    )
    await page.reload()
    await expect(page.getByLabel("站点地址")).toHaveValue(
      "https://synthetic.atlassian.net",
    )
    await page
      .getByLabel("选择工作记录文件")
      .setInputFiles(sample("work_records.csv"))
    await expect(page.getByText("文件导入：成功", { exact: true })).toBeVisible(
      { timeout: 90000 },
    )
    await screenshot("data-ai")
  })

  await test.step("people / identities: manually link a work record account", async () => {
    await page.goto("/employees?filter=identities")
    await page.getByLabel("按来源筛选").selectOption("")
    const identities: ExternalIdentitiesPublic = await get(
      "/external-identities",
    )
    const identity = identities.data.find(
      (row) => row.external_id === "qiao.yanzhi",
    )!
    expect(identity).toBeTruthy()
    const row = page.getByRole("row").filter({ hasText: identity.external_id })
    await row.getByRole("button", { name: "选择员工", exact: true }).click()
    const dialog = page.getByRole("dialog", { name: "选择员工", exact: true })
    await dialog.getByLabel("搜索员工姓名或工号").fill("QG002")
    await dialog.getByRole("button", { name: /在职/ }).click()
    await expect(dialog).toBeHidden()
    await page.reload()
    await page.getByLabel("按来源筛选").selectOption("")
    await page.getByRole("tab", { name: /已关联/ }).click()
    await expect(row).toContainText("乔砚之")
  })

  await test.step("people / appeals: resolve a real rejected decision appeal", async () => {
    const accounts: AccountsPublic = await get("/accounts?q=QG004")
    const reset = await post(
      `/accounts/${accounts.data[0].account!.user_id}/reset-password`,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    const owner = {
      Authorization: `Bearer ${(await changed.json()).access_token}`,
    }
    const draft = await post(
      "/me/requests",
      {
        items: [
          {
            op: "add",
            capability_key: "hardware",
            statement: "合成结构设计",
            owner_statement: "完成了合成评审",
          },
        ],
      },
      owner,
    )
    const submitted = await post(
      `/me/requests/${draft.id}/submit`,
      { revision: draft.revision },
      owner,
    )
    await post(`/reviews/${draft.id}/decisions`, {
      revision: submitted.revision,
      decisions: [
        {
          item_id: draft.items[0].id,
          decision: "rejected",
          reason: "请补充依据",
        },
      ],
    })
    await post(
      "/appeals",
      {
        request_id: draft.id,
        item_id: draft.items[0].id,
        reason: "希望补充合成依据",
      },
      owner,
    )
    await page.goto(`/employees/${accounts.data[0].id}?tab=appeals`)
    await expect(page.getByText("申诉原因：希望补充合成依据")).toBeVisible()
    await page.getByLabel("处理说明").fill("请补充后重新提交")
    await page.getByRole("button", { name: "允许重新提交" }).click()
    await expect(page.getByRole("status")).toContainText("0 项待处理申诉")
    await page.reload()
    expect(await get("/appeals")).toHaveLength(0)
  })

  await test.step("data / model: save a disabled configuration without calling AI", async () => {
    await page.goto("/admin/data-ai?tab=model")
    await page.getByLabel("模型类型").selectOption("openai_compatible")
    await page.getByLabel("模型名", { exact: true }).fill("synthetic-disabled")
    await page.getByLabel(/base URL/).fill("https://model.example.com/v1")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(
      page.getByText("模型设置已保存。", { exact: true }),
    ).toBeVisible()
    await page.reload()
    await expect(page.getByLabel("模型名", { exact: true })).toHaveValue(
      "synthetic-disabled",
    )
    await expect(page.getByLabel("启用模型")).not.toBeChecked()
  })

  await test.step("data / bot: save the question guide", async () => {
    await page.getByRole("tab", { name: "内置机器人", exact: true }).click()
    await page
      .getByLabel("提问指南", { exact: true })
      .fill("先询问员工希望补充的合成经历。")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toContainText("机器人设置已保存")
    await page.reload()
    await expect(page.getByLabel("提问指南", { exact: true })).toHaveValue(
      "先询问员工希望补充的合成经历。",
    )
  })

  await test.step("data / IM: save Feishu credentials without a live connection", async () => {
    await page.getByRole("link", { name: "数据与 AI", exact: true }).click()
    await page.getByRole("tab", { name: "IM 入口", exact: true }).click()
    const form = page.getByRole("form", { name: "飞书", exact: true })
    await form.getByLabel("应用标识", { exact: true }).fill("synthetic-app")
    await form
      .getByLabel("应用密钥", { exact: true })
      .fill("synthetic-app-secret")
    await form.getByRole("checkbox", { name: "启用入口", exact: true }).check()
    await form
      .getByRole("checkbox", { name: "已知晓数据外发提示", exact: true })
      .check()
    await form.getByRole("button", { name: "保存", exact: true }).click()
    await expect(form.getByRole("status")).toContainText("设置已保存")
    await page.reload()
    await expect(
      form.getByRole("checkbox", { name: "启用入口", exact: true }),
    ).toBeChecked()
    await expect(form.getByText(/凭据已设置/)).toBeVisible()
    await expect(form.getByLabel("应用密钥", { exact: true })).toHaveValue("")
    await screenshot("connections")
    await form
      .getByRole("checkbox", { name: "启用入口", exact: true })
      .uncheck()
    await form.getByRole("button", { name: "保存", exact: true }).click()
    await expect(form.getByRole("status")).toContainText("设置已保存")
  })

  await test.step("data / external assistants: save the enterprise switch", async () => {
    await page.getByRole("tab", { name: "外部 AI 助手", exact: true }).click()
    await page.getByRole("checkbox", { name: "允许外部 AI 助手" }).check()
    await page.getByLabel("允许的客户端", { exact: true }).fill("Cursor")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toHaveText("已保存")
    await page.reload()
    await expect(
      page.getByRole("checkbox", { name: "允许外部 AI 助手" }),
    ).toBeChecked()
    await expect(page.getByLabel("允许的客户端", { exact: true })).toHaveValue(
      "Cursor",
    )
  })

  await test.step("home / progress and more / audit: read actual results and filter", async () => {
    await page.getByRole("link", { name: "首页", exact: true }).click()
    const runs = page.getByRole("region", { name: "同步记录" })
    await expect(
      runs.getByRole("row").filter({ hasText: "work_records.csv" }),
    ).toContainText("已完成")
    await page
      .getByRole("region", { name: "今天要处理的事" })
      .getByRole("article", { name: "AI 模型", exact: true })
      .getByRole("link", { name: "去处理", exact: true })
      .click()
    await expect(page).toHaveURL("/admin/data-ai?tab=model")
    await page.getByText("更多", { exact: true }).click()
    await page.getByRole("menuitem", { name: "审计日志", exact: true }).click()
    await page.getByRole("combobox", { name: "操作类型筛选" }).click()
    await page.getByRole("option", { name: "角色", exact: true }).click()
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "乔砚之" })
        .filter({
          has: page.getByRole("cell", { name: "授予角色", exact: true }),
        }),
    ).toContainText("授予角色")
    await expect(
      page.getByRole("cell", { name: "导入名单", exact: true }),
    ).toHaveCount(0)
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    await page
      .getByLabel("结束日期（含当天）")
      .fill(
        `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`,
      )
    await expect(
      page.getByRole("cell", { name: "授予角色", exact: true }),
    ).toHaveCount(0)
    expect(await get("/model-setting")).toMatchObject({
      enabled: false,
      has_api_key: false,
    })
    expect(turns).toBe(0)
  })
})
