import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15000 })

async function setup(
  page: Page,
  request: APIRequestContext,
  extraWorkers: Array<[string, string]> = [],
) {
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
    org_name: "合成变更企业",
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
            `B,合成原员工,研发,M,b-${crypto.randomUUID()}@example.com`,
            `C,合成接替员工,研发,M,c-${crypto.randomUUID()}@example.com`,
            ...extraWorkers.map(
              ([code, name]) =>
                `${code},${name},研发,M,${code.toLowerCase()}-${crypto.randomUUID()}@example.com`,
            ),
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
  for (const code of ["A", "M"]) {
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
  const a = tokens.get("A")!
  const m = tokens.get("M")!
  const workerCodes = ["B", "C", ...extraWorkers.map(([code]) => code)]
  for (const code of workerCodes) {
    await api(
      `${base}/employees/${person(code).id}/profile/direct-edits`,
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
  }
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
      items: workerCodes.map((code) => ({
        employee_id: person(code).id,
        other_percent: 0,
      })),
      start_week: start,
      end_week: later(14),
    },
    m,
    "PUT",
  )
  const create = (description = "合成换人需求", headcount = 1, percent = 70) =>
    api(
      `${base}/capacity/demands`,
      {
        requesting_department_id: person("A").department!.id,
        target_department_id: person("M").department!.id,
        capability_key: "delivery",
        headcount,
        allocation_percent: percent,
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
  const planning = await api(
    `${base}/planning/demands/${demand.id}/cases`,
    {},
    m,
  )
  const initial = await api(
    `${base}/planning/cases/${planning.id}/options`,
    {
      assignees: [
        {
          employee_id: person("B").id,
          allocation_percent: 70,
          start_date: start,
          end_date: later(13),
        },
      ],
    },
    m,
  )
  const proposed = await api(
    `${base}/capacity/demands/${demand.id}/reply`,
    {
      option_id: initial.id,
      revision: demand.revision,
      outcome: "can_do",
      text: "按约定交付",
    },
    m,
  )
  await api(
    `${base}/capacity/demands/${demand.id}/respond`,
    {
      action: "accept",
      revision: proposed.revision,
    },
    a,
  )
  const createLegacy = async () => {
    const legacy = await create("合成手工分配需求")
    const path = `${base}/capacity/demands/${legacy.id}`
    const reply = await api(
      `${path}/reply`,
      {
        revision: legacy.revision,
        outcome: "can_do",
        start_date: start,
        end_date: later(13),
        text: "按约定交付",
      },
      m,
    )
    await api(
      `${path}/respond`,
      { action: "accept", revision: reply.revision },
      a,
    )
    await api(
      `${path}/assignments`,
      {
        employee_id: person("C").id,
        allocation_percent: 70,
        start_date: start,
        due_date: later(13),
      },
      m,
    )
    return legacy.id
  }
  return {
    api,
    base,
    a,
    m,
    create,
    details,
    person,
    createLegacy,
    start,
    later,
    secondWeek: later(7),
  }
}

test("资源方提交换人，需求方匿名确认后替换原分配", async ({
  page,
  request,
}) => {
  const { a, m, details, person } = await setup(page, request)
  await details(m)
  await page.getByRole("button", { name: "发起变更" }).click()
  await page.getByRole("button", { name: "调整投入分段" }).first().click()
  const editor = page.getByRole("form", { name: "编辑投入分段" })
  await editor.getByLabel("人员").first().selectOption(person("C").id)
  await editor.getByRole("button", { name: "模拟调整后的安排" }).click()
  const option = page.getByRole("article", {
    name: "自定方案方案",
    exact: true,
  })
  await option.getByRole("button", { name: "按此方案变更" }).click()
  const form = page.getByRole("form", { name: "发起安排变更" })
  await form.getByLabel("变更理由").fill("调整交付分工")
  await form.getByRole("button", { name: "发送变更提议" }).click()
  await expect(page.getByRole("region", { name: "变更前" })).toContainText(
    "合成原员工",
  )
  await expect(page.getByRole("region", { name: "变更后" })).toContainText(
    "合成接替员工",
  )

  await details(a)
  const proposal = page.getByRole("region", { name: "安排变更提议" })
  await expect(proposal.getByRole("region", { name: "变更前" })).toContainText(
    "投入 70%",
  )
  await expect(proposal.getByRole("region", { name: "变更后" })).toContainText(
    "投入 70%",
  )
  await expect(proposal).not.toContainText(/合成原员工|合成接替员工/)
  await expectChineseCopy(page)
  await page.getByRole("button", { name: "接受变更" }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认接受" })
    .click()
  await expect(
    page
      .getByRole("region", { name: "事件时间线", exact: true })
      .getByText("安排变更已生效"),
  ).toBeVisible()

  await details(m)
  const assignments = page.getByRole("region", { name: "部门内分配" })
  await expect(
    assignments.getByRole("article", { name: "合成原员工" }),
  ).toContainText("已取消")
  await expect(
    assignments.getByRole("article", { name: "合成接替员工" }),
  ).toContainText("进行中")
})

test("已承诺分配从行内发起变更，旧手工分配仍能直接保存开始日期", async ({
  page,
  request,
}) => {
  const { m, details, createLegacy, secondWeek } = await setup(page, request)
  await details(m)
  const assignments = page.getByRole("region", { name: "部门内分配" })
  const committed = assignments.getByRole("article", { name: "合成原员工" })
  await expect(committed).toBeVisible()
  await expect(
    committed.getByRole("form", { name: "修改开始日期" }),
  ).toHaveCount(0)
  await committed.getByRole("link", { name: "发起开始日期变更" }).click()
  const plans = page.getByRole("region", { name: "协调方案" })
  await plans.getByRole("button", { name: "调整投入分段" }).first().click()
  await expect(
    plans.getByRole("form", { name: "编辑投入分段" }).getByLabel("开始日期"),
  ).toBeVisible()

  await details(m, await createLegacy())
  const legacy = assignments.getByRole("article", { name: "合成接替员工" })
  const startForm = legacy.getByRole("form", { name: "修改开始日期" })
  await startForm.getByLabel("调整后的开始日期").fill(secondWeek)
  await startForm.getByRole("button", { name: "保存开始日期" }).click()
  await expect(legacy).toContainText(`开始：${secondWeek}`)
})

test("三人同比例换掉一人时，A方变更前后两栏序号对齐并标出替换项", async ({
  page,
  request,
}) => {
  const s = await setup(page, request, [
    ["D", "合成人员乙"],
    ["E", "合成人员丙"],
  ])
  const multi = await s.create("三人换人需求", 3, 30)
  const makeLines = (codes: string[]) =>
    codes.map((code) => ({
      employee_id: s.person(code).id,
      allocation_percent: 30,
      start_date: s.start,
      end_date: s.later(13),
    }))
  const case1 = await s.api(
    `${s.base}/planning/demands/${multi.id}/cases`,
    {},
    s.m,
  )
  const opt1 = await s.api(
    `${s.base}/planning/cases/${case1.id}/options`,
    { assignees: makeLines(["B", "D", "E"]) },
    s.m,
  )
  const proposed = await s.api(
    `${s.base}/capacity/demands/${multi.id}/reply`,
    {
      option_id: opt1.id,
      revision: multi.revision,
      outcome: "can_do",
      text: "按约定交付",
    },
    s.m,
  )
  const accepted = await s.api(
    `${s.base}/capacity/demands/${multi.id}/respond`,
    { action: "accept", revision: proposed.revision },
    s.a,
  )
  const case2 = await s.api(
    `${s.base}/planning/demands/${multi.id}/cases`,
    {},
    s.m,
  )
  const opt2 = await s.api(
    `${s.base}/planning/cases/${case2.id}/options`,
    { assignees: makeLines(["B", "C", "E"]) },
    s.m,
  )
  await s.api(
    `${s.base}/capacity/demands/${multi.id}/change`,
    {
      option_id: opt2.id,
      reason: "更换其中一位参与人",
      revision: accepted.revision,
    },
    s.m,
  )

  await s.details(s.a, multi.id)
  const proposal = page.getByRole("region", { name: "安排变更提议" })
  const before = proposal.getByRole("region", { name: "变更前" })
  const after = proposal.getByRole("region", { name: "变更后" })
  await expect(before).toContainText("将被更换")
  await expect(after).toContainText("已更换为新人员")

  const beforeHeadings = (await before.locator("h4").allTextContents()).filter(
    (text) => text !== "变更前",
  )
  const afterHeadings = (await after.locator("h4").allTextContents()).filter(
    (text) => text !== "变更后",
  )
  expect(beforeHeadings).toHaveLength(3)
  expect(afterHeadings).toHaveLength(3)

  const beforeReplaced = beforeHeadings.filter((t) => t.includes("将被更换"))
  const afterReplaced = afterHeadings.filter((t) =>
    t.includes("已更换为新人员"),
  )
  expect(beforeReplaced).toHaveLength(1)
  expect(afterReplaced).toHaveLength(1)
  const replacedNumber = beforeReplaced[0].match(/^第 (\d+) 人/)?.[1]
  expect(replacedNumber).toBeTruthy()
  expect(afterReplaced[0]).toBe(`第 ${replacedNumber} 人（已更换为新人员）`)

  const unchangedBefore = beforeHeadings.filter((t) => !t.includes("将被更换"))
  const unchangedAfter = afterHeadings.filter(
    (t) => !t.includes("已更换为新人员"),
  )
  expect(unchangedAfter).toEqual(unchangedBefore)
  expect(unchangedBefore.every((t) => /^第 \d+ 人$/.test(t))).toBeTruthy()

  await expect(proposal).not.toContainText(
    /合成原员工|合成接替员工|合成人员乙|合成人员丙|合成答复经理/,
  )
  await expectChineseCopy(page)
})
