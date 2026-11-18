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
  for (const code of ["B", "C"]) {
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
      items: ["B", "C"].map((code) => ({
        employee_id: person(code).id,
        other_percent: 0,
      })),
      start_week: start,
      end_week: later(14),
    },
    m,
    "PUT",
  )
  const create = (description = "合成换人需求") =>
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
  return { a, m, details, person }
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
  await expect(page.getByText("安排变更已生效")).toBeVisible()

  await details(m)
  const assignments = page.getByRole("region", { name: "部门内分配" })
  await expect(
    assignments.getByRole("article", { name: "合成原员工" }),
  ).toContainText("已取消")
  await expect(
    assignments.getByRole("article", { name: "合成接替员工" }),
  ).toContainText("进行中")
})
