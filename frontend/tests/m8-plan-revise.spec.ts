import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("B 方经理快捷换人并锁定安排", async ({ page, request }) => {
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
            `B3,合成研发丙,研发,M,b3-${crypto.randomUUID()}@example.com`,
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
        { employee_id: person("B3").id, other_percent: 0 },
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
  const direct = created.options.find(
    (option: { kind: string }) => option.kind === "direct",
  )
  expect(direct.verdict).toBe("infeasible")
  const card = page.getByRole("article", { name: "直接安排方案" })
  await card.getByRole("button", { name: "换人" }).click()
  await card.getByLabel("修改人员").selectOption(person("B1").id)
  await card.getByLabel("换成谁").selectOption(person("B3").id)
  await card.getByRole("button", { name: "确认修改" }).click()
  const revised = page.getByRole("article", { name: "自定方案" }).last()
  await expect(revised).toContainText("在「直接安排方案」上修改")
  await expect(revised).toContainText("把合成研发甲换成合成研发丙")
  await expect(revised).toContainText("这次改了什么")
  await expect(revised).toContainText("结论：不可行")
  const weekText = (date: string) =>
    `${Number(date.slice(5, 7))} 月 ${Number(date.slice(8, 10))} 日那一周`
  await expect
    .soft(revised.getByText(/^解决风险：/))
    .toContainText(`合成研发甲：该周超出容量 10% · ${weekText(start)}`)
  await revised.getByRole("button", { name: "锁定", exact: true }).click()
  await revised.getByLabel("修改人员").selectOption(person("B3").id)
  await revised.getByRole("button", { name: "确认修改" }).click()
  const locked = page.getByRole("article", { name: "自定方案" }).last()
  await expect(locked).toContainText("合成研发丙 · 投入 50%")
  await expect(locked).toContainText("已锁定")
  await locked.getByRole("button", { name: "换人" }).click()
  await expect(
    locked.getByLabel("修改人员").locator(`option[value="${person("B3").id}"]`),
  ).toHaveCount(0)
  await locked.getByRole("button", { name: "取消", exact: true }).click()
  await locked.getByRole("button", { name: "锁定", exact: true }).click()
  await locked.getByLabel("修改人员").selectOption(person("B2").id)
  await locked.getByRole("button", { name: "确认修改" }).click()
  await expect(locked.getByText(/已锁定/)).toHaveCount(2)
  for (const name of ["改时间", "降低投入"]) {
    await expect.soft(locked.getByRole("button", { name })).toBeDisabled()
  }
  await expect.soft(locked).toContainText("请先解锁")

  await page.route("**/planning/cases/*/options/*/revise", (route) =>
    route.fulfill({
      status: 422,
      json: {
        detail: [
          { msg: "投入比例必须在允许范围内" },
          { msg: "请检查修改内容" },
        ],
      },
    }),
  )
  await card.getByRole("button", { name: "降低投入" }).click()
  await card.getByRole("button", { name: "确认修改" }).click()
  await expect
    .soft(card.getByRole("alert"))
    .toHaveText("投入比例必须在允许范围内；请检查修改内容")
  await page.unroute("**/planning/cases/*/options/*/revise")
  await card.getByLabel("新的投入比例").fill("5")
  await card.getByRole("button", { name: "确认修改" }).click()
  await expect(card.getByRole("alert")).toHaveText(
    "投入比例须为 10 至 100 的整数",
  )
  await expectChineseCopy(card)
  await card.getByLabel("新的投入比例").fill("80")
  await card.getByRole("button", { name: "确认修改" }).click()
  const increased = page.getByRole("article", { name: "自定方案" }).last()
  await expect(increased).toContainText("投入从50%改到80%")
  await expect
    .soft(increased.getByText(/^新增风险：/))
    .toContainText(`合成研发甲：该周超出容量 20% · ${weekText(later(7))}`)
  const snapshots: string[] = []
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes(`/planning/demands/${demand.id}/cases`)
    )
      snapshots.push(request.url())
  })
  await page.reload()
  await expect(page.getByRole("article").first()).toBeVisible()
  await expect
    .soft(page.getByRole("article", { name: "自定方案" }).last())
    .toContainText("投入从50%改到80%")
  expect.soft(snapshots).toHaveLength(0)
  await expectChineseCopy(page)
  expect(await page.locator("body").textContent()).not.toMatch(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )
})
