import { expect, type Locator, type Page, test } from "@playwright/test"
import type { ConsentInfo } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { setupPlans } from "./utils/plans"
import { logInUser, logOutUser } from "./utils/user"

const desktop = { width: 1440, height: 900 }
const mobile = { width: 390, height: 844 }
const demoPassword = process.env.DEMO_USER_PASSWORD!
const platformEmployee = "employee.platform@demo.example.com"

test.use({ viewport: desktop, storageState: { cookies: [], origins: [] } })

async function expectNoOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() =>
        Math.max(
          document.documentElement.scrollWidth,
          document.body.scrollWidth,
        ),
      ),
    )
    .toBeLessThanOrEqual(page.viewportSize()!.width)
}

async function box(locator: Locator) {
  await expect(locator).toBeVisible()
  return (await locator.boundingBox())!
}

async function expectOneRow(controls: Locator[]) {
  const bounds = await Promise.all(controls.map(box))
  expect(Math.max(...bounds.map(({ y }) => y))).toBeLessThan(
    Math.min(...bounds.map(({ y, height }) => y + height)),
  )
}

async function expectRightDrawer(page: Page, drawer: Locator) {
  await expect(drawer).toHaveAttribute("data-slot", "sheet-content")
  await expect
    .poll(async () => {
      const { x, width } = await box(drawer)
      return Math.round(x + width)
    })
    .toBe(page.viewportSize()!.width)
}

test("inbox keeps quick replies and editing in one card and saves the edited answer", async ({
  page,
  request,
}) => {
  await logInUser(page, platformEmployee, demoPassword)
  // The other inbox spec answers the separately seeded algorithm employee question.
  const questionText = "请描述一次你独立排查线上故障的经历。"
  await page.goto("/inbox")
  const list = page.getByRole("complementary", { name: "消息列表" })
  await list.getByRole("button", { name: new RegExp(questionText) }).click()
  const detail = page.getByRole("region", { name: "消息详情" })
  await expect(
    detail.getByRole("heading", { name: questionText }),
  ).toBeVisible()
  expect((await box(detail)).x).toBeGreaterThanOrEqual(
    (await box(list)).x + (await box(list)).width,
  )

  const form = detail.locator("form")
  await expect(form).toHaveCount(1)
  const quickReply = form
    .getByRole("group", { name: "快捷答复" })
    .getByRole("button", {
      name: "情况属实，我独立负责了相关工作",
    })
  const input = form.getByRole("textbox", { name: "你的回答" })
  const submit = form.getByRole("button", { name: "提交回答", exact: true })
  await expect(
    page.getByRole("button", { name: "提交回答", exact: true }),
  ).toHaveCount(1)
  await expect(submit).toBeDisabled()

  const submissions: string[] = []
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      sent.url().includes("/me/questions/") &&
      sent.url().endsWith("/answer")
    )
      submissions.push(sent.url())
  })
  await quickReply.click()
  await expect(input).toBeFocused()
  await expect(input).toHaveValue("情况属实，我独立负责了相关工作")
  expect(submissions).toHaveLength(0)

  await page.setViewportSize(mobile)
  await expect(list).toBeHidden()
  await expectNoOverflow(page)
  const answer = "情况属实，我独立负责了相关工作，并用合成案例复查了故障恢复。"
  await input.fill(answer)
  await expect(quickReply).toHaveAttribute("aria-pressed", "false")
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes("/me/questions/") &&
      response.url().endsWith("/answer"),
  )
  await submit.click()
  const result = await saved
  expect(result.ok()).toBeTruthy()
  await expect(detail.getByRole("status")).toHaveText(
    "已回答，已保存为受限自述证据",
  )
  expect(submissions).toHaveLength(1)
  const { answer_evidence_id } = await result.json()
  const base = new URL(result.url()).pathname.split("/me/questions/")[0]
  const authorization = await result.request().headerValue("authorization")
  if (!authorization) throw new Error("Missing authorization on inbox reply")
  const evidence = await request.get(`${base}/evidence/${answer_evidence_id}`, {
    headers: { Authorization: authorization },
  })
  expect(evidence.ok()).toBeTruthy()
  expect(await evidence.json()).toMatchObject({
    visibility: "restricted",
    content: { text: answer },
  })
  await expectNoOverflow(page)
})

test("review detail hides empty fields, opens evidence and switches decision reasons", async ({
  page,
  request,
}) => {
  const { api, base, a, admin, open } = await setupPlans(page, request)
  const description = "本人确认的合成审批证据"
  const draft = await api(
    `${base}/me/requests`,
    {
      items: [
        {
          op: "add",
          capability_key: "delivery",
          statement: "合成审批陈述",
          level: "routine",
          owner_statement: description,
        },
      ],
    },
    a,
  )
  await api(
    `${base}/me/requests/${draft.id}/submit`,
    { revision: draft.revision },
    a,
  )
  await open(admin.access_token)
  await page.goto(`/reviews/${draft.id}`)

  const changes = page.getByRole("region", { name: "待审批变更项" })
  await expect(changes).toContainText("合成审批陈述")
  await expectNoOverflow(page)
  await expectChineseCopy(page)
  await expect(changes.getByText(/^(暂无说明|无)$/)).toHaveCount(0)
  await expect(
    changes.getByText(/^(起草说明|待核实点|员工异议)：/),
  ).toHaveCount(0)
  const evidenceChip = changes.getByRole("button")
  await expect(evidenceChip).toHaveCount(1)
  await evidenceChip.click()
  const drawer = page.getByRole("dialog")
  await expectRightDrawer(page, drawer)
  await expect(drawer).toContainText(description)
  await drawer.getByRole("button", { name: "关闭", exact: true }).click()

  const decision = page.getByRole("group", { name: "合成交付的审批意见" })
  const reason = decision.getByRole("textbox", { name: "合成交付的原因" })
  await expect(decision.getByRole("button")).toHaveCount(3)
  await expect(reason).toHaveCount(0)
  for (const label of ["批准", "退回", "拒绝", "批准"]) {
    const selected = decision.getByRole("button", { name: label, exact: true })
    await selected.click()
    await expect(decision.getByRole("button", { pressed: true })).toHaveCount(1)
    await expect(selected).toHaveAttribute("aria-pressed", "true")
    if (label === "批准") await expect(reason).toHaveCount(0)
    else {
      await expect(reason).toBeVisible()
      await expect(reason).toHaveAttribute("required", "")
      await expect(decision.getByText(`${label}原因（必填）`)).toBeVisible()
    }
  }
  await page.setViewportSize(mobile)
  await expect(
    decision.getByRole("button", { name: "批准", exact: true }),
  ).toBeVisible()
  await expectNoOverflow(page)
  await expectChineseCopy(page)
})

test("coverage is a compact row and an employee row opens quick peek before the profile", async ({
  page,
}) => {
  await logInUser(page, "manager.platform@demo.example.com", demoPassword)
  await page.goto("/employees")
  const coverage = page.getByRole("region", { name: "画像覆盖率" })
  const statuses = coverage
    .getByRole("button")
    .and(coverage.locator("[aria-pressed]"))
  await expect(statuses).toHaveCount(5)
  await expectOneRow([
    coverage.getByRole("heading", { name: "画像覆盖率" }),
    ...(await statuses.all()),
    coverage.getByRole("button", { name: "按部门分布" }),
  ])
  const bar = coverage.locator('[aria-hidden="true"]').filter({
    has: page.locator("span[style*=width]"),
  })
  await expect(bar).toBeVisible()
  const published = coverage.getByRole("button", { name: /^已发布/ })
  await published.click()
  await expect(published).toHaveAttribute("aria-pressed", "true")
  await published.click()

  const row = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: "E02", exact: true }),
  })
  await row.getByRole("cell", { name: "E02", exact: true }).click()
  const drawer = page.getByRole("dialog", { name: "员工快速预览" })
  await expectRightDrawer(page, drawer)
  await expect(drawer).toContainText("张伟")
  await expect(page).toHaveURL(/\/employees\/?$/)
  await drawer.getByRole("link", { name: "进入完整画像" }).click()
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
})

test("team workload toolbar stays on one line and the drawer switches directly between cells", async ({
  page,
  request,
}) => {
  const { m, open, start, later } = await setupPlans(page, request)
  await open(m)
  await page.goto("/capacity?tab=team")
  await expectOneRow([
    page.getByRole("combobox", { name: "部门筛选" }),
    page.getByRole("button", { name: "上一期" }),
    page.getByRole("button", { name: "回到本周" }),
    page.getByRole("button", { name: "下一期" }),
    page.getByRole("button", { name: "团队负荷说明和图例" }),
    page.getByRole("button", { name: "按上次设置确认未来 4 周" }),
  ])
  const table = page.getByRole("table", { name: "团队负荷，按部门、姓名排序" })
  const row = table.getByRole("row").filter({ hasText: "合成研发甲" })
  const cells = row.getByRole("button", { name: /查看负荷明细$/ })
  await expect(cells).toHaveCount(8)
  await cells.first().click()
  const drawer = page.getByRole("dialog", { name: "负荷明细 · 合成研发甲" })
  await expectRightDrawer(page, drawer)
  await expect(drawer).not.toHaveAttribute("aria-modal", "true")
  await expect(page.locator('[data-slot="sheet-overlay"]')).toHaveCount(0)
  await expect(drawer).toContainText(`${start} 至 ${later(6)}`)
  await expect(
    drawer.getByRole("button", { name: "登记不可用日期" }),
  ).toBeEnabled()
  await cells.nth(1).click()
  await expect(drawer).toContainText(`${later(7)} 至 ${later(13)}`)
  await expect(cells.first()).toHaveAttribute("aria-pressed", "false")
  await expect(cells.nth(1)).toHaveAttribute("aria-pressed", "true")
  await expectNoOverflow(page)
})

test("planning switches board and list views and creates a demand in a right drawer", async ({
  page,
  request,
}) => {
  const { a, open, person, start, later } = await setupPlans(page, request)
  await open(a)
  await page.goto("/capacity?tab=borrows")
  const list = page.getByRole("button", { name: "紧凑列表" })
  const board = page.getByRole("button", { name: "状态看板" })
  const selection = page.getByRole("region", { name: "待选人", exact: true })
  await expect(selection).toBeVisible()
  await list.click()
  await expect(selection).toHaveCount(0)
  await expect(list).toHaveAttribute("aria-pressed", "true")
  await board.click()
  await expect(selection).toBeVisible()

  const navigation = page.getByRole("navigation", { name: "需求工作台" })
  await navigation.getByRole("link", { name: "已发出的需求" }).click()
  const demands = page.getByRole("region", { name: "已发出的需求" })
  const pending = demands.getByRole("region", { name: "待答复", exact: true })
  await expect(pending).toContainText("合成方案需求")
  await list.click()
  await expect(pending).toHaveCount(0)
  await expect(
    demands.getByRole("article").filter({ hasText: "合成方案需求" }),
  ).toBeVisible()
  await board.click()
  await expect(pending).toContainText("合成方案需求")

  await page.getByRole("button", { name: "新建需求", exact: true }).click()
  const drawer = page.getByRole("dialog", { name: "新建协作需求" })
  await expectRightDrawer(page, drawer)
  await drawer.getByLabel("需求方部门").selectOption(person("A").department!.id)
  await drawer.getByLabel("目标部门").selectOption(person("M").department!.id)
  await drawer.getByLabel("能力", { exact: true }).selectOption("delivery")
  await drawer.getByLabel("人数", { exact: true }).fill("1")
  await drawer.getByLabel("每人投入比例（%）").fill("40")
  await drawer.getByLabel("开始日期").fill(start)
  await drawer.getByLabel("结束日期").fill(later(6))
  const description = "合成重构回归需求"
  await drawer.getByLabel("需求说明").fill(description)
  await drawer.getByRole("button", { name: "预览需求" }).click()
  await expect(drawer.getByRole("region", { name: "需求预览" })).toContainText(
    description,
  )
  await drawer.getByRole("button", { name: "确认提交需求" }).click()
  await expect(drawer).toBeHidden()
  const detail = page.getByRole("region", { name: "需求详情" })
  await expect(detail).toContainText(description)
  const timeline = detail.getByRole("region", { name: "事件时间线" })
  const properties = detail.getByRole("complementary", {
    name: "需求属性与操作",
  })
  await expect(timeline.getByRole("article")).toContainText(description)
  await expect(properties).toContainText("待答复")
  const left = await box(timeline)
  expect((await box(properties)).x).toBeGreaterThanOrEqual(left.x + left.width)
  await page.setViewportSize(mobile)
  await expect(
    detail.getByRole("button", { name: "查看需求属性与操作" }),
  ).toBeVisible()
  await expectNoOverflow(page)
})

test("business action returns to its topbar slot after a working assistant conversation", async ({
  page,
  request,
}) => {
  const { a, open } = await setupPlans(page, request)
  await open(a)
  await page.goto("/capacity?tab=outgoing")
  const slot = page.locator("header #workspace-topbar-actions")
  const action = page.getByRole("button", { name: "新建需求", exact: true })
  await expect(
    slot.getByRole("button", { name: "新建需求", exact: true }),
  ).toBeEnabled()
  await expect(action).toHaveCount(1)
  await page.getByRole("link", { name: "去 AI 助手", exact: true }).click()
  await expect(page).toHaveURL(/\/assistant(?:\?|$)/)
  await expect(slot).toHaveCount(0)
  await expect(action).toHaveCount(0)

  await page.route("**/bot/sessions/*/turns?*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response: { text: "已收到合成回归消息", cards: [], stopped: false } })}\n`,
    }),
  )
  const panel = page.getByRole("region", { name: "助手", exact: true })
  const input = panel.getByRole("textbox", { name: "发给助手的消息" })
  await input.fill("合成回归消息")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await expect(panel.getByRole("log")).toContainText("已收到合成回归消息")
  await expect(input).toHaveValue("")
  await expectNoOverflow(page)
  await page.setViewportSize(mobile)
  await expect(input).toBeInViewport()
  await expectNoOverflow(page)
  await page.setViewportSize(desktop)

  await page.goBack()
  await expect(page).toHaveURL("/capacity?tab=outgoing")
  await expect(action).toHaveCount(1)
  const restoredAction = slot.getByRole("button", {
    name: "新建需求",
    exact: true,
  })
  await restoredAction.click()
  await expect(page.getByRole("dialog", { name: "新建协作需求" })).toBeVisible()
})

for (const viewport of [desktop, mobile]) {
  test(`primary non-assistant routes have Chinese copy and fit ${viewport.width}×${viewport.height}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000)
    await page.setViewportSize(viewport)
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text())
    })
    const visit = async (
      path: string,
      title: string,
      terms: string[] = [],
      fixtureValues: string[] = [],
    ) => {
      await test.step(path.split("#")[0], async () => {
        await page.goto(path)
        await expect(
          page.getByRole("heading", { name: title, exact: true, level: 1 }),
        ).toBeVisible()
        await expect(
          page.getByRole("status", { name: "正在加载" }),
        ).toHaveCount(0)
        await page.waitForLoadState("networkidle")
        await page.evaluate(() => document.fonts.ready.then(() => undefined))
        await expectNoOverflow(page)
        await expectChineseCopy(page, terms, fixtureValues)
        expect(errors).toEqual([])
      })
    }
    await visit("/login", "登录员工画像平台")
    const setup = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    expect(setup.ok()).toBeTruthy()
    await visit(`/setup#token=${(await setup.json()).token}`, "初始化企业")

    await logInUser(page, "admin@demo.example.com", demoPassword)
    for (const [path, title, terms] of [
      ["/inbox", "收件箱", ["AI"]],
      ["/reviews", "待审批", ["AI", "MCP"]],
      ["/employees", "员工", ["AI"]],
      ["/org-chart", "组织架构", ["AI"]],
      ["/admin/rules?tab=dictionary", "能力词典", ["AI", "SQL", "Python"]],
      ["/admin/rules?tab=approval", "审批规则", ["AI"]],
      ["/admin/rules?tab=reminders", "定期提醒", ["AI"]],
      ["/admin/rules?tab=capacity", "负荷与需求规则", ["AI"]],
      ["/settings?section=account", "账号与安全", ["AI"]],
      ["/settings?section=integrations", "连接与集成", ["AI", "MCP"]],
      ["/settings?section=preferences", "偏好与代理", ["AI"]],
      [
        "/admin/data-ai?tab=sources",
        "数据来源",
        ["AI", "Atlassian", "Cloud", "Data Center"],
      ],
      ["/admin/data-ai?tab=im", "IM 入口", ["AI", "Telegram"]],
      ["/admin/data-ai?tab=mcp", "外部 AI 工具", ["AI", "MCP"]],
      [
        "/admin/data-ai?tab=model",
        "模型",
        [
          "AI",
          "OpenAI",
          "ChatGPT",
          "Anthropic",
          "Claude",
          "Google",
          "Gemini",
          "API Key",
        ],
      ],
      ["/admin/data-ai?tab=bot", "内置助手", ["AI"]],
      ["/admin/audit", "审计日志", ["AI"]],
    ] as const) {
      await visit(path, title, [...terms])
    }

    await logOutUser(page)
    await logInUser(page, platformEmployee, demoPassword)
    const consent = {
      client_name: "合成测试工具",
      org_name: "合成授权企业",
      callback_host: "client.example",
      reason: null,
    } satisfies ConsentInfo
    await page.route("**/api/v1/oauth/consent?*", (route) =>
      route.fulfill({ json: consent }),
    )
    await visit(
      "/oauth/consent?request=synthetic",
      "合成测试工具 申请访问你的账号",
      [],
      [consent.callback_host],
    )
    await expect(
      page
        .getByRole("list", { name: "授权后，该工具可以" })
        .getByRole("listitem"),
    ).toHaveCount(3)

    await page.getByRole("link", { name: "组织", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "张伟", exact: true, level: 1 }),
    ).toBeVisible()
    await visit(new URL(page.url()).pathname, "张伟")

    await logOutUser(page)
    const { m, open, demand } = await setupPlans(page, request)
    await open(m)
    await visit("/capacity?tab=mine", "我的负荷")
    await expect(
      page.getByRole("button", { name: "申请调整", exact: true }),
    ).toBeInViewport({ ratio: 1 })
    await visit("/capacity?tab=team", "团队负荷")
    await visit("/capacity?tab=borrows", "借调请求")
    await visit("/capacity?tab=outgoing", "协作需求")
    await expect(
      page.getByRole("button", { name: "新建项目", exact: true }),
    ).toBeInViewport({ ratio: 1 })
    await visit("/capacity?tab=projects", "项目需求")
    await visit(`/capacity?tab=incoming&demand=${demand.id}`, "协作需求")
  })
}
