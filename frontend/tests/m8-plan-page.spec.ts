import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("B 方经理无需模型查看方案，A 方经理看不到入口", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
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
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/planning/demands/${demand.id}/cases`) &&
      response.request().method() === "POST",
  )
  await open(m)
  await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
  const created = await (await createdResponse).json()
  const card = page.getByRole("article", { name: "直接安排方案" })
  await expect(card).toBeVisible()
  const current = await api(
    `${base}/planning/cases/${created.id}`,
    undefined,
    m,
  )
  const direct = current.options.find(
    (option: { kind: string }) => option.kind === "direct",
  )
  expect(direct).toBeTruthy()
  await expect(card).toContainText(direct.kind_label)
  await expect(card).toContainText(direct.verdict_label)
  await expect(card).toContainText(`交付日期：${direct.summary.finish_date}`)
  await expect(card).toContainText(
    `覆盖 ${direct.summary.covered_headcount} 人`,
  )
  await expect(card).toContainText(
    `还差 ${direct.summary.gap_person_weeks} 人周`,
  )
  await expect(card).toContainText("合成研发甲")
  await expect(card).toContainText("合成研发乙")
  await expect(card).toContainText("已投入 20%")
  await expect(card).toContainText("风险")
  expect(direct.findings.length).toBeGreaterThan(0)
  expect(direct.summary.affected_assignment_ids.length).toBeGreaterThan(0)
  await expect(card).toContainText(direct.findings[0].message)
  await expect(card).toContainText(start)
  await expect(card).toContainText(later(13))
  await expect(card).toContainText("本部门经理确认答复")
  await expectChineseCopy(page)
  expect(await page.locator("body").textContent()).not.toMatch(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("B1").id, other_percent: 30 }],
      start_week: start,
      end_week: later(14),
    },
    m,
    "PUT",
  )
  await page.getByRole("button", { name: "关闭方案" }).click()
  await page.getByRole("button", { name: "生成方案 / 查看方案" }).click()
  await expect(page.getByText("数据已变化，请重新模拟")).toBeVisible()
  await page.getByRole("button", { name: "重新模拟" }).click()
  await expect(page.getByText("数据已变化，请重新模拟")).toHaveCount(0)
  const caseUrl = `**/planning/cases/${created.id}`
  const conflict = "部分候选人已不在当前管辖范围内，请重新模拟"
  await page.route(caseUrl, async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: conflict }),
    })
  })
  await page.reload()
  await expect(page.getByRole("alert")).toContainText(conflict, {
    timeout: 15000,
  })
  await page.unroute(caseUrl)
  await page.getByRole("button", { name: "重新模拟" }).click()
  await expect(card).toBeVisible()
  await open(a)
  await expect(
    page.getByRole("button", { name: /生成方案|查看方案/ }),
  ).toHaveCount(0)
  await expect(page.locator("body")).not.toContainText("合成研发甲")
})
