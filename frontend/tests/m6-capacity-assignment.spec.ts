import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { importRoster } from "./utils/roster"

test.use({ storageState: { cookies: [], origins: [] } })

test("分配、延期审批及推迟承诺通知，A 方始终只见部门信息", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-assignment-password-2026"
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
    org_name: "合成分配企业",
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
  const open = async (token: string, tab: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(`/capacity?tab=${tab}`)
    await expect(
      page.getByRole("heading", { name: "需求与容量", exact: true }),
    ).toBeVisible()
  }
  await open(admin.access_token, "team")
  await importRoster(
    page,
    Buffer.from(
      `工号,姓名,部门,上级工号,邮箱\nA,合成客户,客户,,a@example.com\nM,合成经理,研发,,m@example.com\nB1,合成研发甲,研发,M,b1@example.com\nB2,合成研发乙,研发,M,b2@example.com\n`.replace(
        /@example\.com/g,
        `-${crypto.randomUUID()}@example.com`,
      ),
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
  for (const code of ["A", "M", "B1"]) {
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
    b = tokens.get("B1")!
  for (const code of ["B1", "B2"])
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
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("B1").id, other_percent: 20 }],
      start_week: (
        await api(`${base}/capacity/workloads/me`, undefined, m, "GET")
      ).weeks[0].week_start,
      end_week: (
        await api(`${base}/capacity/workloads/me`, undefined, m, "GET")
      ).weeks[0].week_start,
    },
    m,
    "PUT",
  )
  const demand = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 2,
      allocation_percent: 50,
      window_start: "2026-12-01",
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
      start_date: "2026-12-01",
      end_date: "2026-12-25",
      text: "可以承接",
    },
    m,
  )
  await api(
    `${base}/capacity/demands/${demand.id}/respond`,
    { action: "accept", revision: reply.revision },
    a,
  )
  const details = async (token: string, side: string) => {
    await open(token, side)
    await page.getByRole("button", { name: "查看详情", exact: true }).click()
  }
  await details(m, "incoming")
  const assignment = page.getByRole("region", { name: "部门内分配" })
  for (const code of ["B1", "B2"]) {
    await assignment
      .getByLabel("从依据中选择员工")
      .selectOption(person(code).id)
    await assignment
      .getByLabel("预计完成日期", { exact: true })
      .fill("2026-12-20")
    await assignment.getByRole("button", { name: "确认分配" }).click()
    await expect(
      assignment.getByRole("article", { name: person(code).display_name }),
    ).toBeVisible()
  }
  const workload = async (percent: string) => {
    await open(m, "team")
    await expect(
      page.getByRole("row").filter({ hasText: "合成研发甲" }),
    ).toContainText(percent)
  }
  await workload("70%")
  const inboxCount = async () =>
    (await api(`${base}/me/inbox`, undefined, a)).count
  const before = await inboxCount()
  const extension = async (date: string) => {
    await open(b, "assignments")
    await page.getByLabel("申请完成日期").fill(date)
    await page.getByLabel("延期原因").fill("合成内部原因")
    await page.getByRole("button", { name: "提交延期申请" }).click()
    await expect(page.getByRole("status")).toContainText(
      "已提交，等待 合成经理 审批",
    )
    await expect(
      page.getByRole("button", { name: "提交延期申请", exact: true }),
    ).toHaveCount(0)
  }
  await extension("2026-12-23")
  await open(m, "approvals")
  await expect(page.getByLabel("新的承诺完成日期")).toHaveCount(0)
  await page.getByRole("button", { name: "同意延期", exact: true }).click()
  await expect(page.getByRole("region", { name: "延期审批" })).toContainText(
    "暂无待处理",
  )
  expect(await inboxCount()).toBe(before)
  await open(b, "assignments")
  await expect(
    page.getByRole("region", { name: "我的分配", exact: true }),
  ).toContainText("2026-12-23")
  await expect(
    page.getByRole("region", { name: "延期申请记录" }),
  ).toContainText("已同意")
  await extension("2026-12-28")
  await open(m, "approvals")
  await page.getByLabel("处理方式").selectOption("return")
  await page.getByRole("button", { name: "退回延期", exact: true }).click()
  expect(
    await page
      .getByLabel("处理说明（退回必填）")
      .evaluate(
        (element: HTMLTextAreaElement) => element.validity.valueMissing,
      ),
  ).toBeTruthy()
  await expect(page.getByLabel("新的承诺完成日期")).toHaveCount(0)
  await page.getByLabel("处理说明（退回必填）").fill("请先确认依赖方时间")
  await page.getByRole("button", { name: "退回延期", exact: true }).click()
  await expect(page.getByRole("region", { name: "延期审批" })).toContainText(
    "暂无待处理",
  )
  await open(b, "assignments")
  await expect(
    page.getByRole("region", { name: "延期申请记录" }),
  ).toContainText("请先确认依赖方时间")
  await extension("2026-12-28")
  await open(m, "approvals")
  await page.getByRole("button", { name: "同意延期", exact: true }).click()
  await expect(page.getByLabel("新的承诺完成日期")).toHaveJSProperty(
    "value",
    "",
  )
  await page.getByLabel("新的承诺完成日期").fill("2026-12-30")
  await page.getByLabel("给对方部门的说明").fill(" ")
  await page.getByRole("button", { name: "同意延期", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText(
    "String should have at least 1 character",
  )
  await page.getByLabel("给对方部门的说明").fill("合成研发甲需要更多时间")
  await page.getByRole("button", { name: "同意延期", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("请不要写本部门员工")
  await page.getByLabel("给对方部门的说明").fill("接口变更，需要延后交付")
  await page.getByRole("button", { name: "同意延期", exact: true }).click()
  await expect(page.getByRole("region", { name: "延期审批" })).toContainText(
    "暂无待处理",
  )
  expect(await inboxCount()).toBeGreaterThan(before)
  await details(a, "outgoing")
  await expect(
    page.getByRole("region", { name: "需求详情", exact: true }),
  ).toContainText("2026-12-30")
  await expect(page.getByRole("region", { name: "事件时间线" })).toContainText(
    "承诺日期由 2026-12-25 改为 2026-12-30",
  )
  await expect(page.getByRole("region", { name: "事件时间线" })).toContainText(
    "接口变更，需要延后交付",
  )
  for (const text of ["合成研发甲", "合成研发乙", "合成内部原因"])
    await expect(page.locator("body")).not.toContainText(text)
  await expect(page.getByRole("region", { name: "部门内分配" })).toHaveCount(0)
  await details(m, "incoming")
  const first = assignment.getByRole("article", { name: "合成研发甲" })
  await first.getByLabel("调整后的完成日期").fill("2027-01-02")
  await first.getByLabel("新的承诺完成日期").fill("2027-01-04")
  await first.getByLabel("给对方部门的说明").fill("合成最终交付调整")
  await first.getByRole("button", { name: "保存完成日期" }).click()
  await expect(first).toContainText("2027-01-02")
  await first.getByRole("button", { name: "标记完成" }).click()
  await expect(first).toContainText("已完成")
  await workload("20%")
  await details(m, "incoming")
  await assignment
    .getByRole("article", { name: "合成研发乙" })
    .getByRole("button", { name: "取消分配" })
    .click()
  await expect(
    assignment.getByRole("article", { name: "合成研发乙" }),
  ).toContainText("已取消")
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: test.info().outputPath("assignment-mobile.png"),
    fullPage: true,
  })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
})
