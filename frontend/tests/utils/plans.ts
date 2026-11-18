import { type APIRequestContext, expect, type Page } from "@playwright/test"
import type { EmployeeAccount } from "../../src/client"

export async function setupPlans(
  page: Page,
  request: APIRequestContext,
  withReplacement = false,
) {
  const password = "synthetic-plan-password-2026"
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
    org_name: "合成方案企业",
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
            `B1,合成研发甲,研发,M,b1-${crypto.randomUUID()}@example.com`,
            `B2,合成研发乙,研发,M,b2-${crypto.randomUUID()}@example.com`,
            ...(withReplacement
              ? [`B3,合成研发丙,研发,M,b3-${crypto.randomUUID()}@example.com`]
              : []),
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
    headers: { Authorization: `Bearer ${admin.access_token}` },
    data: {},
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
      {
        current_password: reset.initial_password,
        new_password: password,
      },
      (await login.json()).access_token,
      "PATCH",
    )
    tokens.set(code, changed.access_token)
  }
  for (const code of ["B1", "B2"]) {
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
  const a = tokens.get("A")!
  const m = tokens.get("M")!
  const start = (await api(`${base}/capacity/workloads/me`, undefined, m))
    .weeks[0].week_start as string
  const later = (days: number) =>
    new Date(Date.parse(`${start}T12:00:00Z`) + days * 86400000)
      .toISOString()
      .slice(0, 10)
  await api(
    `${base}/capacity/weeks`,
    {
      items: [
        { employee_id: person("B1").id, other_percent: 40 },
        { employee_id: person("B2").id, other_percent: 0 },
        ...(withReplacement
          ? [{ employee_id: person("B3").id, other_percent: 0 }]
          : []),
      ],
      start_week: start,
      end_week: later(14),
    },
    m,
    "PUT",
  )
  const demandBody = {
    requesting_department_id: person("A").department!.id,
    target_department_id: person("M").department!.id,
    capability_key: "delivery",
    allocation_percent: 50,
    window_start: start,
    window_end: later(13),
  }
  const old = await api(
    `${base}/capacity/demands`,
    { ...demandBody, headcount: 1, description: "合成已有工作" },
    a,
  )
  const reply = await api(
    `${base}/capacity/demands/${old.id}/reply`,
    {
      revision: old.revision,
      outcome: "can_do",
      start_date: start,
      end_date: later(13),
      text: "可以承接",
    },
    m,
  )
  await api(
    `${base}/capacity/demands/${old.id}/respond`,
    { action: "accept", revision: reply.revision },
    a,
  )
  await api(
    `${base}/capacity/demands/${old.id}/assignments`,
    {
      employee_id: person("B1").id,
      allocation_percent: 20,
      start_date: start,
      due_date: later(6),
    },
    m,
  )
  const demand = await api(
    `${base}/capacity/demands`,
    { ...demandBody, headcount: 2, description: "合成方案需求" },
    a,
  )
  const open = async (token: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(`/capacity?tab=incoming&demand=${demand.id}&plans=1`)
  }
  return { api, base, person, a, m, start, later, demand, open, admin }
}
