import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("普通代理、兜底管理员和经理都能从待我审批处理申请", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-approvals-password-2026"
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
    if (response.status() === 204) return null
    return response.headers()["content-type"]?.includes("application/json")
      ? response.json()
      : null
  }
  const setup = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成审批企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const root = admin.access_token
  await api(`${base}/capacity-settings`, { enabled: true }, root, "PUT")
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${root}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nM,合成经理,研发,,m@example.com\nE,合成员工,研发,M,e@example.com\nD,合成代理,研发,,d@example.com\nA,合成客户,客户,,a@example.com\nF,合成兜底员工,无经理/下级,,f@example.com\n`.replace(
            /@example\.com/g,
            `-${crypto.randomUUID()}@example.com`,
          ),
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  await api(
    `${base}/roster/imports/${preview.id}/apply`,
    {
      base_revision: preview.base_revision,
    },
    root,
  )
  await api(`${base}/accounts/issue`, {}, root)
  const accounts: EmployeeAccount[] = (
    await api(`${base}/accounts`, undefined, root)
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
    root,
  )
  const tokens = new Map<string, string>()
  for (const code of ["M", "E", "D", "A", "F"]) {
    const reset = await api(
      `${base}/accounts/${person(code).account!.user_id}/reset-password`,
      {},
      root,
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
  const m = tokens.get("M")!,
    e = tokens.get("E")!,
    d = tokens.get("D")!,
    a = tokens.get("A")!
  const delegate = async (code: string) => {
    const view = await api(
      `${base}/me/review-delegations`,
      {
        delegate_user_id: person(code).account!.user_id,
        starts_at: new Date(Date.now() - 60000).toISOString(),
        ends_at: new Date(Date.now() + 3600000).toISOString(),
      },
      m,
    )
    return view.data.find(
      (row: { delegate_user_id: string }) =>
        row.delegate_user_id === person(code).account!.user_id,
    )!.id
  }
  const delegationId = await delegate("D")
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
    root,
  )
  const demand = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 30,
      window_start: "2026-12-01",
      window_end: "2026-12-31",
      description: "合成需求",
    },
    a,
  )
  const reply = await api(
    `${base}/capacity/demands/${demand.id}/reply`,
    {
      revision: demand.revision,
      outcome: "can_do",
      start_date: "2026-12-01",
      end_date: "2026-12-31",
      text: "可以承接",
    },
    m,
  )
  await api(
    `${base}/capacity/demands/${demand.id}/respond`,
    { action: "accept", revision: reply.revision },
    a,
  )
  const assignment = await api(
    `${base}/capacity/demands/${demand.id}/assignments`,
    {
      employee_id: person("E").id,
      allocation_percent: 30,
      due_date: "2026-12-20",
    },
    m,
  )
  const workloadRequest = async (token: string, percent: number) =>
    api(
      `${base}/capacity/workloads/me/requests`,
      {
        requested_percent: percent,
        reason: "合成调整原因",
      },
      token,
    )
  const extensionRequest = async () =>
    api(
      `${base}/capacity/assignments/${assignment.id}/extension-requests`,
      {
        requested_due_date: "2026-12-23",
        reason: "合成私有延期原因",
      },
      e,
    )
  await workloadRequest(e, 60)
  await extensionRequest()
  const open = async (token: string, path = "/capacity?tab=approvals") => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(path)
  }
  const teamReads: string[] = []
  page.on("request", (req) => {
    if (new URL(req.url()).pathname === `${base}/capacity/workloads`)
      teamReads.push(req.url())
  })
  await open(e)
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("form", { name: "合成员工的延期申请" }),
  ).toHaveCount(0)
  await open(d)
  await expect(page.getByRole("tab", { name: "待我审批" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
  await expect(page.getByRole("tab", { name: "团队负荷" })).toHaveCount(0)
  await expect(
    page.getByRole("article", { name: "员工的调整申请" }),
  ).toContainText("申请 60%")
  await expect(
    page.getByRole("form", { name: "合成员工的延期申请" }),
  ).toBeVisible()
  expect(teamReads).toEqual([])
  await page.screenshot({
    path: test.info().outputPath("approvals.png"),
    fullPage: true,
  })
  await page.goto("/inbox")
  for (const message of ["有负荷调整申请等你审批", "有延期申请等你审批"]) {
    const link = page
      .getByRole("listitem")
      .filter({ hasText: message })
      .getByRole("link", { name: "查看需求与容量" })
    await expect(link).toHaveAttribute("href", "/capacity?tab=approvals")
    await link.click()
    await expect(page.getByRole("tab", { name: "待我审批" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    await page.goto("/inbox")
  }
  await page.goto("/capacity?tab=approvals")
  await page
    .getByRole("article", { name: "员工的调整申请" })
    .getByRole("button", { name: "同意", exact: true })
    .click()
  const extension = page.getByRole("form", { name: "合成员工的延期申请" })
  await extension.getByLabel("处理方式").selectOption("return")
  await extension.getByLabel("处理说明（退回必填）").fill("请补充依赖")
  await extension.getByRole("button", { name: "退回延期" }).click()
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await open(e, "/capacity?tab=mine")
  await expect(page.getByText("90%", { exact: true })).toBeVisible()
  await page.goto("/capacity?tab=assignments")
  await expect(
    page.getByRole("region", { name: "延期申请记录" }),
  ).toContainText("请补充依赖")
  await expect(
    page.getByRole("region", { name: "我的分配", exact: true }),
  ).toContainText("2026-12-20")
  await page.goto("/inbox")
  await expect(
    page.getByRole("link", { name: "查看需求与容量" }).first(),
  ).toHaveAttribute("href", "/capacity")
  await page.getByRole("link", { name: "查看需求与容量" }).first().click()
  await expect(page).toHaveURL("/capacity")
  await page.goto("/capacity?tab=approvals")
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await workloadRequest(tokens.get("F")!, 40)
  const [membership] = await api("/api/v1/me/memberships", undefined, root)
  expect(membership.managed_department_ids).toEqual([])
  await open(root)
  await page
    .getByRole("article", { name: "员工的调整申请" })
    .getByRole("button", { name: "同意", exact: true })
    .click()
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await open(tokens.get("F")!, "/capacity?tab=mine")
  await expect(page.getByText("40%", { exact: true })).toBeVisible()
  await workloadRequest(e, 50)
  await open(m)
  await page
    .getByRole("article", { name: "员工的调整申请" })
    .getByRole("button", { name: "同意", exact: true })
    .click()
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await page.goto("/capacity?tab=team")
  await expect(page.getByRole("region", { name: "延期审批" })).toHaveCount(0)
  await expect(
    page.getByRole("region", { name: "待处理的调整申请" }),
  ).toHaveCount(0)
  await api(
    `${base}/me/review-delegations/${delegationId}`,
    undefined,
    m,
    "DELETE",
  )
  await delegate("A")
  await extensionRequest()
  await open(a)
  await expect(
    page.getByText("目前没有需要你审批的申请", { exact: true }),
  ).toBeVisible()
  await expect(page.locator("body")).not.toContainText("合成员工")
  await expect(page.locator("body")).not.toContainText("合成私有延期原因")
  expect(
    (await api(`${base}/capacity/extension-requests`, undefined, a)).data,
  ).toEqual([])
})
