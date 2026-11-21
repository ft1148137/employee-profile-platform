import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test"
import type {
  CapacityCard,
  EmployeeAccount,
  SessionMessage,
} from "../src/client"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15000 })

async function setup(page: Page, request: APIRequestContext) {
  const password = "synthetic-borrow-password-2026"
  const api = async (
    url: string,
    data?: object,
    token?: string,
    method = data ? "POST" : "GET",
  ) => {
    const response = await request.fetch(url, {
      method,
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const setup = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成借调企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${admin.access_token}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          [
            "工号,姓名,部门,上级工号,邮箱",
            `A,合成需求经理,客户,,a-${crypto.randomUUID()}@example.com`,
            `M,合成答复经理,研发,,m-${crypto.randomUUID()}@example.com`,
            `L,合成借出经理,交付支持,,l-${crypto.randomUUID()}@example.com`,
            `B,合成借调员工,交付支持,L,b-${crypto.randomUUID()}@example.com`,
          ].join("\n"),
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await api(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    admin.access_token,
  )
  await api(
    `${base}/capabilities/versions`,
    {
      expected_version: 0,
      definitions: [
        {
          key: "delivery",
          name: "合成交付",
          category: "工程",
          description: "合成能力",
          level_rubric: {
            levels: [
              { key: "routine", label: "独立完成", description: "能独立完成" },
            ],
          },
        },
      ],
    },
    admin.access_token,
  )
  const issued = await request.post(`${base}/accounts/issue`, {
    data: {},
    headers: { Authorization: `Bearer ${admin.access_token}` },
  })
  expect(issued.ok()).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await api(`${base}/accounts`, undefined, admin.access_token)
  ).data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  const tokens = new Map<string, string>()
  for (const code of ["A", "M", "L", "B"]) {
    if (code !== "B")
      await api(
        `${base}/role-grants`,
        {
          user_id: person(code).account!.user_id,
          role: "dept_manager",
          department_id: person(code).department!.id,
        },
        admin.access_token,
      )
    const reset = await api(
      `${base}/accounts/${person(code).account!.user_id}/reset-password`,
      {},
      admin.access_token,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await api(
      "/api/v1/users/me/password",
      { current_password: reset.initial_password, new_password: password },
      (await login.json()).access_token,
      "PATCH",
    )
    tokens.set(code, changed.access_token)
  }
  const a = tokens.get("A")!,
    m = tokens.get("M")!,
    lender = tokens.get("L")!,
    employee = tokens.get("B")!
  await api(
    `${base}/employees/${person("B").id}/profile/direct-edits`,
    {
      base_revision: null,
      items: [
        {
          op: "add",
          capability_key: "delivery",
          statement: "合成工作",
          level: "routine",
        },
      ],
    },
    admin.access_token,
  )
  await api(
    `${base}/capacity-settings`,
    { enabled: true, requester_policy: "everyone" },
    admin.access_token,
    "PUT",
  )
  const start = (await api(`${base}/capacity/workloads/me`, undefined, m))
    .weeks[0].week_start as string
  const later = (days: number) =>
    new Date(Date.parse(`${start}T12:00:00Z`) + days * 86400000)
      .toISOString()
      .slice(0, 10)
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("B").id, other_percent: 0 }],
      start_week: start,
      end_week: later(14),
    },
    lender,
    "PUT",
  )
  const create = (description = "合成借调需求") =>
    api(
      `${base}/capacity/demands`,
      {
        requesting_department_id: person("A").department!.id,
        target_department_id: person("M").department!.id,
        capability_key: "delivery",
        headcount: 1,
        allocation_percent: 70,
        window_start: start,
        window_end: later(13),
        description,
      },
      a,
    )
  const demand = await create()
  const open = async (token: string, search: string, path = "/capacity") => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(search ? `${path}?${search}` : path)
  }
  return {
    a,
    m,
    lender,
    employee,
    demand,
    open,
    department: person("L").department!.id,
    admin,
    api,
    base,
    start,
    workerEmail: person("B").work_email!,
  }
}

async function acceptInAssistant({
  page,
  baseURL,
  setupResult: { a, admin, api, base, demand, open },
}: {
  page: Page
  baseURL: string | undefined
  setupResult: Awaited<ReturnType<typeof setup>>
}) {
  const path = `/capacity?tab=outgoing&demand=${demand.id}`
  const pending = await api(
    `${base}/capacity/demands/${demand.id}`,
    undefined,
    a,
  )
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const done = JSON.parse(raw).messages.at(-1).role === "tool"
    const delta = done
      ? { content: "请核对借调方案。https://example.invalid/" }
      : {
          tool_calls: [
            {
              index: 0,
              id: "prepare-accept",
              type: "function",
              function: {
                name: "prepare_demand_response",
                arguments: JSON.stringify({
                  action: "accept",
                  demand_id: demand.id,
                  revision: pending.revision,
                }),
              },
            },
          ],
        }
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({
        id: "borrow-response",
        object: "chat.completion.chunk",
        choices: [
          { index: 0, delta, finish_reason: done ? "stop" : "tool_calls" },
        ],
      })}\n\ndata: [DONE]\n\n`,
    )
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  try {
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing model address")
    const host =
      process.env.MODEL_TEST_HOST ??
      (baseURL?.includes("backend:")
        ? Object.values(networkInterfaces())
            .flat()
            .find((network) => network?.family === "IPv4" && !network.internal)
            ?.address
        : "localhost")
    await api(
      `${base}/model-setting`,
      {
        enabled: true,
        kind: "openai_compatible",
        provider: "openai-compatible",
        model: "synthetic",
        base_url: `http://${host}:${address.port}/v1`,
        api_key: "synthetic-key",
      },
      admin.access_token,
      "PUT",
    )
    await open(a, "mode=personal", "/assistant")
    const panel = page.getByRole("region", { name: "助手", exact: true })
    await panel.getByLabel("发给助手的消息").fill("请准备接受借调方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel.getByRole("region", { name: "回应部门需求" })
    await expect(card).toContainText("暂留截止")
    await expect(panel.getByText(/https:\/\/example\.invalid/)).toBeVisible()
    await expect(
      panel.getByRole("link", { name: /example\.invalid/ }),
    ).toHaveCount(0)
    await card.getByRole("link", { name: "查看需求详情" }).click()
    await expect(page).toHaveURL(path)
    await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
      "合成借调需求",
    )
    const timeline = page.getByRole("region", { name: "事件时间线" })
    await expect(timeline).toContainText("答复人：对方部门经理")
    await expect(timeline).toContainText("需要协调一人")
    await expect(page.getByRole("main")).not.toContainText("合成借调员工")
    await expect(page.getByRole("button", { name: "接受方案" })).toBeVisible()
    await page.goBack()
    await card.getByRole("button", { name: "确认", exact: true }).click()
    await expect(card.last().getByRole("status")).toHaveText(
      `需求回应已提交，编号：${demand.id}`,
    )
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
}

test("需求确认卡用结构化链接导航并过滤站外地址", async ({ page, request }) => {
  const { a, demand, open } = await setup(page, request)
  const session = crypto.randomUUID()
  const href = `/capacity?tab=outgoing&demand=${demand.id}`
  const card = {
    type: "capacity_card",
    kind: "demand_response",
    title: "新版确认卡",
    content: "请核对这次借调方案。",
    arguments: { demand_id: demand.id },
  } satisfies CapacityCard
  const messages: SessionMessage[] = [
    {
      id: crypto.randomUUID(),
      role: "assistant",
      created_at: new Date().toISOString(),
      payload: {
        messages: [
          { ...card, detail_link: { label: "打开需求", href } },
          ...["https://example.invalid/", "//example.invalid/"].map(
            (externalHref, index) => ({
              ...card,
              title: `站外地址示例 ${index + 1}`,
              detail_link: { label: "打开需求", href: externalHref },
            }),
          ),
          { ...card, title: "历史确认卡", content: "历史卡片的正文" },
        ],
      },
    },
  ]
  await page.route(`**/bot/sessions/${session}/messages?*`, (route) =>
    route.fulfill({ json: messages }),
  )
  await open(a, `mode=personal&session=${session}`, "/assistant")
  const latest = page.getByRole("region", { name: "新版确认卡" })
  await expect(latest).toContainText(card.content)
  for (const title of ["站外地址示例 1", "站外地址示例 2", "历史确认卡"]) {
    const other = page.getByRole("region", { name: title })
    await expect(other).toBeVisible()
    await expect(other.getByRole("link")).toHaveCount(0)
  }
  await expect(page.getByText("历史卡片的正文")).toBeVisible()
  await latest.getByRole("link", { name: "打开需求" }).click()
  await expect(page).toHaveURL(href)
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "合成借调需求",
  )
})

for (const approve of [true, false]) {
  test(
    approve
      ? "借出经理在借调页跟踪并完成分配，借入经理仅看匿名进度"
      : "本人不同意后仅本人看得到理由",
    async ({ page, request, baseURL }) => {
      test.setTimeout(120000)
      const setupResult = await setup(page, request)
      const {
        a,
        m,
        lender,
        employee,
        demand,
        open,
        department,
        admin,
        api,
        base,
        start,
        workerEmail,
      } = setupResult
      const incoming = `tab=incoming&demand=${demand.id}`
      const outgoing = `tab=outgoing&demand=${demand.id}`
      await open(m, `${incoming}&plans=1`)
      const borrow = page.getByRole("article", { name: "向其他部门借调方案" })
      await borrow.getByLabel("借出部门").selectOption(department)
      await borrow.getByRole("button", { name: "按此方案答复" }).click()
      await page.getByLabel("答复说明").fill("需要协调一人")
      await page.getByRole("button", { name: "确认发送答复" }).click()
      const proposal = page.getByRole("region", { name: "方案提议" })
      await expect(proposal).toContainText("原部门已同意 0/1")

      await open(lender, "tab=borrows")
      const ready = page.getByRole("region", { name: "待选人", exact: true })
      await ready.getByRole("button", { name: "选择人选" }).click()
      const selectionDrawer = page.getByRole("dialog", { name: "合成交付" })
      await expect(selectionDrawer.getByLabel("借调员工")).toBeVisible()
      await page.keyboard.press("Escape")
      await expect(selectionDrawer).toHaveCount(0)

      await open(lender, "", "/inbox")
      const lenderNotice = page.getByRole("listitem").filter({
        has: page.getByText(/^借调请求（合成交付，/),
      })
      await expect(
        lenderNotice.getByText("未读", { exact: true }),
      ).toBeVisible()
      await lenderNotice.getByRole("button").click()
      const messageDetail = page.getByRole("region", { name: "消息详情" })
      await messageDetail.getByRole("link", { name: "查看待审批" }).click()
      await expect(page).toHaveURL("/reviews?type=borrow")
      await page.getByText("待确认人选", { exact: true }).click()
      const selection = page
        .getByRole("region", { name: "借调确认" })
        .getByRole("article")
        .filter({ hasText: "合成交付 · 待选人" })
      await selection
        .getByLabel("借调员工")
        .selectOption({ label: "合成借调员工" })
      await selection.getByRole("button", { name: "确认人选" }).click()
      await expect(selection).toHaveCount(0)
      await page.goBack()
      await expect(lenderNotice).toBeVisible()
      await expect(lenderNotice.getByText("未读", { exact: true })).toHaveCount(
        0,
      )
      await open(lender, "tab=borrows")
      const confirmation = page.getByRole("region", {
        name: "待确认",
        exact: true,
      })
      await expect(confirmation.getByText("待员工确认")).toBeVisible()
      await page.getByRole("button", { name: "紧凑列表" }).click()
      await expect(confirmation).toHaveCount(0)
      await expect(page.getByText("待员工确认")).toBeVisible()

      if (approve) {
        await open(employee, "", "/inbox")
        const notice = page
          .locator("li")
          .filter({ hasText: "借调请求待你确认" })
          .first()
        await expect(notice).toBeVisible()
        await notice.getByRole("button").click()
        await messageDetail.getByRole("link", { name: "查看待审批" }).click()
        await expect(page).toHaveURL("/reviews?type=borrow")
        await expect(
          page.getByRole("link", { name: /^借调确认/ }),
        ).toHaveAttribute("aria-current", "page")
        await page.getByText("待本人确认", { exact: true }).click()
      } else {
        await open(employee, "tab=assignments")
      }
      const card = page.getByRole("region", {
        name: approve ? "借调确认" : "借调请求",
        exact: true,
      })
      await expect(
        card.getByRole("button", { name: "同意", exact: true }),
      ).toBeVisible()
      await expect(
        card.getByRole("button", { name: "不同意", exact: true }),
      ).toBeVisible()
      if (approve) {
        await card.getByRole("button", { name: "同意", exact: true }).click()
        await expect(card).not.toContainText("待你确认")
        await open(employee, "tab=mine")
        await expect(
          page.getByRole("region", { name: "借调请求", exact: true }),
        ).toContainText("等待生效")
        await acceptInAssistant({ page, baseURL, setupResult })
        await open(a, outgoing)
        await expect(proposal).toContainText("借调已生效")

        const taskKey = "SYN-BORROW-TRACK"
        const taskTitle = "合成借调交付"
        const imported = await request.post(`${base}/sources/csv/imports`, {
          headers: { Authorization: `Bearer ${admin.access_token}` },
          multipart: {
            file: {
              name: "tasks.csv",
              mimeType: "text/csv",
              buffer: Buffer.from(
                [
                  "记录编号,来源,发生时间,执行人账号,标题,状态",
                  `${taskKey},合成工作台,${start}T09:00:00+08:00,${workerEmail},${taskTitle},已完成`,
                ].join("\n"),
              ),
            },
          },
        })
        expect(imported.ok(), await imported.text()).toBeTruthy()
        await expect
          .poll(
            async () =>
              (await api(`${base}/sync-runs`, undefined, admin.access_token))
                .data[0]?.status,
            { timeout: 30_000 },
          )
          .toBe("succeeded")

        await open(employee, "tab=assignments")
        const personal = page.getByRole("region", {
          name: "我的分配",
          exact: true,
        })
        await personal.getByText("任务明细", { exact: true }).click()
        await personal.getByLabel("任务编号或标题").fill(taskKey)
        await personal.getByRole("button", { name: "搜索任务" }).click()
        await personal
          .getByRole("button", { name: "关联任务", exact: true })
          .click()
        const personalTasks = personal.getByRole("list", {
          name: "已关联任务",
        })
        await expect(personalTasks).toContainText(taskKey)
        await personal.getByRole("button", { name: "取消关联" }).click()
        await expect(personalTasks).toHaveCount(0)

        await open(lender, "", "/inbox")
        const trackingNotice = page
          .getByRole("listitem")
          .filter({ hasText: /分配任务跟踪.*尚未关联任务/ })
        await expect(trackingNotice).toContainText(/分配 #[0-9a-f]{8}（/)
        await expect(trackingNotice).not.toContainText("/capacity?tab=borrows")
        await trackingNotice.getByRole("button").click()
        await messageDetail.getByRole("link", { name: "打开借调页" }).click()
        await expect(page).toHaveURL(/\/capacity\?tab=borrows$/)
        await expect(
          page.getByRole("link", { name: "借调请求", exact: true }),
        ).toBeVisible()
        const active = page.getByRole("region", { name: "进行中的借调分配" })
        const assignment = active
          .getByRole("article")
          .filter({ hasText: "合成借调员工" })
        await expect(assignment).toContainText("合成借调需求")
        await assignment.getByLabel("任务编号或标题").fill(taskKey)
        await assignment.getByRole("button", { name: "搜索任务" }).click()
        await assignment
          .getByRole("button", { name: "关联任务", exact: true })
          .click()
        const linked = assignment.getByRole("list", { name: "已关联任务" })
        await expect(linked).toContainText(`${taskKey} · ${taskTitle}`)

        await open(m, "", "/inbox")
        await trackingNotice.getByRole("button").click()
        await messageDetail.getByRole("link", { name: "打开方案页" }).click()
        await expect(page).toHaveURL(`/capacity?${incoming}&plans=1`)
        const assignments = page.getByRole("region", { name: "部门内分配" })
        await expect(assignments).toContainText("借调 1 人")
        await expect(assignments).toContainText(
          "关联任务已全部完成，可以结束分配",
        )
        await expect(assignments).not.toContainText("合成借调员工")
        await expect(assignments).not.toContainText(taskKey)
        await expect(assignments.getByLabel("任务编号或标题")).toHaveCount(0)
        await expect(
          assignments.getByRole("button", { name: "标记完成" }),
        ).toHaveCount(0)

        await open(lender, "")
        await page.getByRole("link", { name: "借调请求", exact: true }).click()
        await expect(linked).toContainText(taskKey)
        await linked.getByRole("button", { name: "取消关联" }).click()
        await expect(linked).toHaveCount(0)
        await expect(
          assignment.getByText("未关联任务", { exact: true }),
        ).toBeVisible()
        await assignment.getByLabel("任务编号或标题").fill(taskKey)
        await assignment.getByRole("button", { name: "搜索任务" }).click()
        await assignment
          .getByRole("button", { name: "关联任务", exact: true })
          .click()
        await expect(assignment).toContainText(
          "关联任务已全部完成，可以结束分配",
        )

        const complete = assignment.getByRole("button", { name: "标记完成" })
        const completed = page.getByRole("region", { name: "已结束的借调分配" })
        await page.route(
          new RegExp(`${base}/capacity/assignments/[0-9a-f-]+$`),
          (route) =>
            route.fulfill({
              status: 409,
              json: { detail: "合成测试：暂时无法结束分配" },
            }),
          { times: 1 },
        )
        await complete.click()
        await expect(assignment.getByRole("alert")).toHaveText(
          "合成测试：暂时无法结束分配",
        )
        await expect(assignment).toBeVisible()
        await expect(completed.getByRole("article")).toHaveCount(0)
        await complete.click()
        await expect(active.getByRole("article")).toHaveCount(0)
        await expect(completed).toContainText("合成借调员工")
        await completed.getByRole("button", { name: "写反馈" }).click()
        await expect(
          completed.getByLabel("工作反馈（20～500 字）"),
        ).toBeVisible()

        await open(m, incoming)
        await expect(
          assignments.getByRole("article").filter({ hasText: "借调 1 人" }),
        ).toContainText("已完成")
        await expect(assignments).not.toContainText("合成借调员工")
        await expect(assignments).not.toContainText(taskKey)
        await expect(
          assignments.getByRole("button", { name: "写反馈" }),
        ).toHaveCount(0)
      } else {
        await card.getByLabel("拒绝理由").fill("仅本人可见的合成理由")
        await card.getByLabel("拒绝原因").selectOption("priority")
        await card.getByRole("button", { name: "不同意", exact: true }).click()
        await expect(card).toContainText("仅本人可见的合成理由")
        for (const [token, search] of [
          [a, outgoing],
          [m, incoming],
        ]) {
          await open(token, search)
          await expect(proposal).toContainText("借调未达成")
          await expect(page.locator("body")).not.toContainText(
            "仅本人可见的合成理由",
          )
          await expect(proposal).not.toContainText("合成借调员工")
        }
      }
    },
  )
}
