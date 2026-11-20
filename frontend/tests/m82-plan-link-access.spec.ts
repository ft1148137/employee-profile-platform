import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })
test.setTimeout(120000)

async function setup(
  request: APIRequestContext,
  status: "accepted" | "submitted" = "accepted",
) {
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
  const password = "synthetic-plan-access-2026"
  const setupToken = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setupToken.token,
    org_name: "合成方案权限企业",
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
            `A,合成需求经理,客户,,${crypto.randomUUID()}@example.com`,
            `M,合成答复经理,研发,,${crypto.randomUUID()}@example.com`,
            `C,合成下级经理,研发/下级,M,${crypto.randomUUID()}@example.com`,
            `B,合成研发员工,研发,M,${crypto.randomUUID()}@example.com`,
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
  expect(issued.ok(), await issued.text()).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await api(`${base}/accounts`, undefined, admin.access_token)
  ).data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  const tokens = new Map<string, string>()
  for (const code of ["A", "M", "C"]) {
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
  const start = (
    await api(`${base}/capacity/workloads/me`, undefined, tokens.get("M"))
  ).weeks[0].week_start as string
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("B").id, other_percent: 0 }],
      start_week: start,
      end_week: start,
    },
    tokens.get("M"),
    "PUT",
  )
  const demand = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 20,
      window_start: start,
      window_end: start,
      description: "合成权限测试需求",
    },
    tokens.get("A"),
  )
  const data = { base, orgId: admin.org_id as string, demand, tokens }
  if (status === "submitted") return data
  const reply = await api(
    `${base}/capacity/demands/${demand.id}/reply`,
    {
      revision: demand.revision,
      outcome: "can_do",
      start_date: start,
      end_date: start,
      text: "可以承接",
    },
    tokens.get("M"),
  )
  await api(
    `${base}/capacity/demands/${demand.id}/respond`,
    { action: "accept", revision: reply.revision },
    tokens.get("A"),
  )
  await api(
    `${base}/capacity/demands/${demand.id}/assignments`,
    {
      employee_id: person("B").id,
      allocation_percent: 20,
      start_date: start,
      due_date: start,
    },
    tokens.get("M"),
  )
  return data
}

async function open(
  page: Page,
  data: Awaited<ReturnType<typeof setup>>,
  role: string,
) {
  await page.goto("/login")
  await page.evaluate(
    ({ token, org }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", org)
    },
    { token: data.tokens.get(role)!, org: data.orgId },
  )
  await page.goto(`/capacity?tab=incoming&demand=${data.demand.id}&plans=1`)
}

for (const status of ["submitted", "accepted"] as const) {
  test(`需求方直开 ${status} 方案有说明，可返回本条需求进度且不请求个人数据`, async ({
    page,
    request,
  }) => {
    const data = await setup(request, status)
    const privateRequests: string[] = []
    page.on("request", (request) => {
      if (/\/(basis|assignments|cases)(\?|$)/.test(request.url()))
        privateRequests.push(request.url())
    })
    await open(page, data, "A")
    await expect(
      page.getByText(
        "这条需求由 研发 答复和安排人员，方案和分配只有该部门经理能看。",
      ),
    ).toBeVisible()
    await expect(page.getByText("出错了")).toHaveCount(0)
    await expect(page.getByRole("form", { name: "新增分配" })).toHaveCount(0)
    await expect(page.getByRole("form", { name: "答复需求" })).toHaveCount(0)
    await expect(
      page.getByText(/合成研发员工|合成答复经理|合成下级经理/),
    ).toHaveCount(0)
    await page.getByRole("button", { name: "到我提的需求查看进度" }).click()
    await expect(page).toHaveURL(
      new RegExp(`tab=outgoing&demand=${data.demand.id}$`),
    )
    await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
      "合成权限测试需求",
    )
    await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
      status === "accepted" ? "已接受" : "待答复",
    )
    if (status === "accepted")
      await expect(
        page.getByRole("region", { name: "事件时间线" }),
      ).toContainText("可以承接")
    await page.getByRole("button", { name: "返回需求入口" }).click()
    await expect(
      page.getByRole("region", { name: "已发出的需求" }),
    ).toContainText("研发")
    await page.goBack()
    await page.goBack()
    await expect(
      page.getByRole("region", { name: "方案查看范围" }),
    ).toContainText("这条需求由 研发 答复和安排人员")
    expect(privateRequests).toEqual([])
  })
}

test("下级经理直开方案只见权限说明和返回入口，不显示需求或部门", async ({
  page,
  request,
}) => {
  const data = await setup(request)
  const response = page.waitForResponse(
    `${data.base}/capacity/demands/${data.demand.id}`,
  )
  await open(page, data, "C")
  expect((await response).status()).toBe(404)
  await expect(
    page.getByText(
      "未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。",
    ),
  ).toBeVisible()
  await expect(page.getByText("出错了")).toHaveCount(0)
  await expect(page.getByRole("region", { name: "需求详情" })).toHaveCount(0)
  await expect(
    page.getByText(
      /合成权限测试需求|合成研发员工|合成答复经理|合成下级经理|研发/,
    ),
  ).toHaveCount(0)
  await page.getByRole("button", { name: "返回待审批" }).click()
  await expect(
    page.getByRole("region", { name: "协作需求", exact: true }),
  ).toBeVisible()
  await expect(page).toHaveURL("/reviews?type=demand")
})

test("目标部门经理直开方案仍可查看方案、依据和分配", async ({
  page,
  request,
}) => {
  const data = await setup(request)
  await open(page, data, "M")
  await expect(
    page.getByRole("article", { name: "直接安排方案" }),
  ).toBeVisible()
  await expect(
    page.getByRole("region", { name: "本部门匹配人员" }),
  ).toContainText("合成研发员工")
  await expect(page.getByRole("region", { name: "部门内分配" })).toContainText(
    "合成研发员工",
  )
  await expect(page.getByRole("form", { name: "新增分配" })).toBeVisible()
  await expect(page.getByRole("region", { name: "方案查看范围" })).toHaveCount(
    0,
  )
})

for (const status of [403, 404, 500]) {
  test(`局部接口返回 ${status} 时区分权限提示与普通故障`, async ({
    page,
    request,
  }) => {
    const data = await setup(request)
    await page.route(
      /\/(capacity\/demands\/[^/]+\/(basis|assignments)|planning\/)/,
      (route) => route.fulfill({ status, json: { detail: "合成接口错误" } }),
    )
    await open(page, data, "M")
    if (status === 500) {
      await expect(
        page
          .getByRole("region", { name: "本部门匹配人员" })
          .getByText("出错了"),
      ).toBeVisible({ timeout: 15000 })
      await expect(
        page.getByRole("region", { name: "部门内分配" }).getByText("出错了"),
      ).toHaveCount(2)
      await expect(
        page.getByRole("region", { name: "协调方案" }),
      ).toContainText("合成接口错误")
    } else {
      for (const name of ["本部门匹配人员", "协调方案", "部门内分配"]) {
        await expect(
          page
            .getByRole("region", { name })
            .getByText("你现在没有权限查看这部分"),
        ).toBeVisible({ timeout: 15000 })
      }
      await expect(page.getByText("出错了")).toHaveCount(0)
      await expect(page.getByRole("form", { name: "新增分配" })).toHaveCount(0)
      await expect(page.getByRole("button", { name: "重新模拟" })).toHaveCount(
        0,
      )
    }
  })
}
