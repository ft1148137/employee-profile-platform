import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { importRoster } from "./utils/roster"

test.use({ storageState: { cookies: [], origins: [] } })

test("旧分配表单收到离职冲突后提示并刷新候选人", async ({ page, request }) => {
  test.setTimeout(120000)
  const password = "synthetic-departed-assignment-2026"
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
    return response.headers()["content-type"]?.includes("application/json")
      ? response.json()
      : null
  }
  const setup = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成离职分配企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  await api(
    `${base}/capacity-settings`,
    { enabled: true, requester_policy: "everyone" },
    admin.access_token,
    "PUT",
  )
  await page.goto("/login")
  await page.evaluate(
    ({ token, org }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", org)
    },
    { token: admin.access_token, org: admin.org_id },
  )
  await page.goto("/capacity?tab=team")
  await importRoster(
    page,
    Buffer.from(
      `工号,姓名,部门,上级工号,邮箱\nA,合成需求方,需求,,a-${crypto.randomUUID()}@example.com\nM,合成承接经理,承接,,m-${crypto.randomUUID()}@example.com\nB,合成候选人,承接,M,b-${crypto.randomUUID()}@example.com\n`,
    ),
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
              { key: "routine", label: "独立完成", description: "独立完成" },
            ],
          },
        },
      ],
    },
    admin.access_token,
  )
  await api(`${base}/accounts/issue`, {}, admin.access_token)
  const accounts: EmployeeAccount[] = (
    await api(`${base}/accounts`, undefined, admin.access_token)
  ).data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  await api(
    `${base}/role-grants`,
    {
      user_id: person("M").account!.user_id,
      role: "dept_manager",
      department_id: person("M").department!.id,
    },
    admin.access_token,
  )
  const tokens = new Map<string, string>()
  for (const code of ["A", "M"]) {
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
  const a = tokens.get("A")!,
    m = tokens.get("M")!
  const startWeek = (await api(`${base}/capacity/workloads/me`, undefined, m))
    .weeks[0].week_start
  const demand = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 20,
      window_start: startWeek,
      window_end: "2026-12-31",
      description: "合成联调需求",
    },
    a,
  )
  const reply = await api(
    `${base}/capacity/demands/${demand.id}/reply`,
    {
      revision: demand.revision,
      outcome: "can_do",
      start_date: startWeek,
      end_date: "2026-12-25",
      text: "可以承接",
    },
    m,
  )
  await api(
    `${base}/capacity/demands/${demand.id}/respond`,
    {
      action: "accept",
      revision: reply.revision,
    },
    a,
  )

  await page.goto("/login")
  await page.evaluate(
    ({ token, org }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", org)
    },
    { token: m, org: admin.org_id },
  )
  let basisReads = 0
  let departed = false
  await page.route(
    `**${base}/capacity/demands/${demand.id}/basis`,
    async (route) => {
      basisReads += 1
      const response = await route.fetch()
      const body = await response.json()
      if (departed) {
        body.data = body.data.filter(
          (row: { employee_id: string }) => row.employee_id !== person("B").id,
        )
        body.others = body.others.filter(
          (row: { employee_id: string }) => row.employee_id !== person("B").id,
        )
        body.count = body.data.length
      }
      await route.fulfill({ response, json: body })
    },
  )
  await page.route(
    `**${base}/capacity/demands/${demand.id}/assignments`,
    async (route) => {
      if (route.request().method() === "POST") {
        departed = true
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              code: "employee_departed",
              message: "该员工已不在职，请刷新后重新选择",
            },
          }),
        })
      } else await route.continue()
    },
  )
  await page.goto(`/capacity?tab=incoming&demand=${demand.id}`)
  const assignment = page.getByRole("region", { name: "部门内分配" })
  await assignment.getByLabel("从依据中选择员工").selectOption(person("B").id)
  await assignment.getByLabel("开始日期", { exact: true }).fill(startWeek)
  await assignment
    .getByLabel("预计完成日期", { exact: true })
    .fill("2026-12-20")
  await assignment.getByRole("checkbox", { name: /该员工不在依据中/ }).check()
  const readsBefore = basisReads
  await assignment.getByRole("button", { name: "确认分配" }).click()
  await expect(assignment.getByRole("alert")).toContainText(
    "该员工已不在职，请刷新后重新选择",
  )
  await expect(
    assignment.getByRole("link", { name: "去确认团队负荷" }),
  ).toHaveCount(0)
  await expect.poll(() => basisReads).toBeGreaterThan(readsBefore)
  await expect(
    assignment
      .getByLabel("从依据中选择员工")
      .locator(`option[value="${person("B").id}"]`),
  ).toHaveCount(0)
})
