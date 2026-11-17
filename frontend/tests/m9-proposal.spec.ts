import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15000 })

async function setup(page: Page, request: APIRequestContext) {
  const password = "synthetic-proposal-password-2026"
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
    org_name: "合成协商企业",
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
            `B,合成研发员工,研发,M,b-${crypto.randomUUID()}@example.com`,
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
  for (const code of ["A", "M", "B"]) {
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
    m,
    "PUT",
  )
  const create = (description = "合成协商需求") =>
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
  const open = async (token: string, search: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(`/capacity?${search}`)
  }
  const details = (token = m, id = demand.id, plans = false) =>
    open(
      token,
      `tab=${token === a ? "outgoing" : "incoming"}&demand=${id}${plans ? "&plans=1" : ""}`,
    )
  const prepare = async (split = false, id = demand.id) => {
    const plan = await api(`${base}/planning/demands/${id}/cases`, {}, m)
    const option = await api(
      `${base}/planning/cases/${plan.id}/options`,
      {
        assignees: (split
          ? [
              { start_date: start, end_date: later(6), allocation_percent: 30 },
              {
                start_date: later(7),
                end_date: later(13),
                allocation_percent: 50,
              },
            ]
          : [{ start_date: start, end_date: later(13), allocation_percent: 70 }]
        ).map((line) => ({ ...line, employee_id: person("B").id })),
      },
      m,
    )
    await details(m, id)
    // An explicit hash must read the original option without creating a snapshot.
    await page.goto(
      `/capacity?tab=incoming&demand=${id}&plans=1#case=${plan.id}`,
    )
    return { plan, option }
  }
  const send = async () => {
    await expect(
      page
        .getByRole("article", { name: "自定方案方案", exact: true })
        .getByRole("button", { name: "按此方案答复" }),
    ).toBeVisible()
    await page
      .getByRole("article", { name: "自定方案方案", exact: true })
      .getByRole("button", { name: "按此方案答复" })
      .click()
    await page
      .getByRole("form", { name: "答复需求" })
      .getByLabel("答复说明")
      .fill("按约定日期交付，分段需要对方确认")
    await page.getByRole("button", { name: "确认发送答复" }).click()
    await expect(
      page.getByRole("region", { name: "方案提议", exact: true }),
    ).toContainText("等待需求方确认")
  }
  const privatePage = async () => {
    const text = await page.locator("body").textContent()
    expect(text).not.toContain("合成研发员工")
    expect(text).not.toContain("合成答复经理")
    expect(text).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    )
    await expectChineseCopy(page)
  }
  return {
    api,
    base,
    a,
    m,
    employee,
    demand,
    person,
    create,
    open,
    details,
    prepare,
    send,
    privatePage,
    start,
    later,
  }
}

test("按方案答复预占，接受后逐段生效并通知员工", async ({ page, request }) => {
  test.setTimeout(120000)
  const s = await setup(page, request)
  await s.prepare(true)
  await s.send()
  await s.open(s.m, "tab=team")
  const row = page.getByRole("row").filter({ hasText: "合成研发员工" })
  await expect(row).toContainText("预占 30%")
  await expect(row).toContainText("预占 50%")
  await expect(row).toContainText("分配 0%")
  await expect(row).toContainText("前待对方确认")
  await s.details(s.a)
  const proposal = page.getByRole("region", { name: "方案提议", exact: true })
  await expect(proposal).toContainText("1 人")
  await expect(proposal).toContainText("第 1 人")
  await expect(proposal).toContainText(`投入 30% · ${s.start} 至 ${s.later(6)}`)
  await expect(proposal).toContainText(
    `投入 50% · ${s.later(7)} 至 ${s.later(13)}`,
  )
  await s.privatePage()
  await proposal.screenshot({
    path: test.info().outputPath("anonymous-proposal.png"),
  })
  await page.getByRole("button", { name: "接受方案", exact: true }).click()
  await expect(page.getByRole("dialog")).toContainText(
    "接受后对方部门的人员投入会被锁定并生成分配",
  )
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认接受" })
    .click()
  await expect(proposal).toContainText("已生效")
  await s.privatePage()
  await s.details()
  await expect(proposal).toContainText("合成研发员工")
  await expect(proposal).toContainText("1 人")
  const assignments = page.getByRole("region", { name: "部门内分配" })
  await expect(assignments.getByRole("article")).toHaveCount(2)
  await expect(assignments).toContainText("投入 30%")
  await expect(assignments).toContainText("投入 50%")
  await expect(page.getByRole("form", { name: "新增分配" })).toHaveCount(0)
  await s.open(s.employee, "tab=assignments")
  await expect(page.getByRole("article")).toHaveCount(2)
  await expect(page.locator("body")).toContainText("30%")
  await expect(page.locator("body")).toContainText("50%")
  await s.open(s.m, "tab=team")
  await expect(row).toContainText("分配 30%")
  await expect(row).toContainText("分配 50%")
  await expect(row).not.toContainText("前待对方确认")
  await expectChineseCopy(page)
})

test("拒绝理由必填，拒绝后可重新规划且释放预占", async ({ page, request }) => {
  test.setTimeout(120000)
  const s = await setup(page, request)
  await s.prepare()
  await s.send()
  await s.details(s.a)
  await page.getByRole("button", { name: "拒绝方案", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("拒绝后对方需要重新安排")
  await expect(dialog.getByRole("button", { name: "确认拒绝" })).toBeDisabled()
  await dialog.getByLabel("拒绝理由").fill("交付条件需要调整")
  await dialog.getByRole("button", { name: "确认拒绝" }).click()
  await expect(
    page.getByRole("region", { name: "方案提议", exact: true }),
  ).toContainText("被拒")
  await s.privatePage()
  await s.details()
  await expect(page.locator("body")).toContainText("交付条件需要调整")
  await page.getByRole("button", { name: "重新规划", exact: true }).click()
  await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
  await s.open(s.m, "tab=team")
  await expect(
    page.getByRole("row").filter({ hasText: "合成研发员工" }),
  ).not.toContainText("前待对方确认")
})

test("同周争抢给出来源和超出比例，撤回后旧确认显示当前状态", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const s = await setup(page, request)
  const first = await s.prepare()
  const second = await s.create("合成后提交需求")
  await s.prepare(false, second.id)
  await page
    .getByRole("article", { name: "自定方案方案", exact: true })
    .getByRole("button", { name: "按此方案答复" })
    .click()
  await page.getByLabel("答复说明").fill("按约定日期交付")
  await s.api(
    `${s.base}/capacity/demands/${s.demand.id}/reply`,
    {
      option_id: first.option.id,
      revision: s.demand.revision,
      outcome: "can_do",
      text: "按期交付",
    },
    s.m,
  )
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("alert")).toContainText("超出 40%")
  await expect(page.getByRole("alert")).toContainText("合成协商需求")
  await expectChineseCopy(page)
  await s.details(s.a)
  await expect(
    page.getByRole("button", { name: "接受方案", exact: true }),
  ).toBeVisible()
  const current = await s.api(
    `${s.base}/capacity/demands/${s.demand.id}`,
    undefined,
    s.m,
  )
  await s.api(
    `${s.base}/capacity/proposals/${current.proposal.id}/withdraw`,
    { revision: current.proposal.revision },
    s.m,
  )
  await page.getByRole("button", { name: "接受方案", exact: true }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认接受" })
    .click()
  await expect(page.getByRole("alert")).toContainText("已撤回")
  await s.privatePage()
  await s.details()
  await expect(
    page.getByRole("region", { name: "方案提议", exact: true }),
  ).toContainText("已撤回")
  await expect(
    page.getByRole("button", { name: "重新规划", exact: true }),
  ).toBeVisible()
})

test("撤回提议需要再次确认，过期方案不可答复", async ({ page, request }) => {
  test.setTimeout(120000)
  const s = await setup(page, request)
  await s.prepare()
  await s.send()
  await page.getByRole("button", { name: "撤回提议", exact: true }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "取消", exact: true })
    .click()
  await expect(
    page.getByRole("region", { name: "方案提议", exact: true }),
  ).toContainText("等待需求方确认")
  await page.getByRole("button", { name: "撤回提议", exact: true }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认撤回" })
    .click()
  await expect(
    page.getByRole("region", { name: "方案提议", exact: true }),
  ).toContainText("已撤回")
  const { plan } = await s.prepare()
  await s.api(
    `${s.base}/capacity/weeks`,
    {
      items: [{ employee_id: s.person("B").id, other_percent: 10 }],
      start_week: s.start,
      end_week: s.later(14),
    },
    s.m,
    "PUT",
  )
  await page.reload()
  await expect(page.getByText("数据已变化，请重新模拟")).toBeVisible()
  await expect(
    page
      .getByRole("article", { name: "自定方案方案", exact: true })
      .getByRole("button", { name: "按此方案答复" }),
  ).toBeDisabled()
  expect(plan.id).toBeTruthy()
})
