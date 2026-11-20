import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("员工关联已完成的任务，经理看到可以结束分配", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const password = "synthetic-tracking-password-2026"
  const headers = (token: string) => ({ Authorization: `Bearer ${token}` })
  const api = async (
    url: string,
    token?: string,
    data?: object,
    method = data ? "POST" : "GET",
  ) => {
    const response = await request.fetch(url, {
      method,
      data,
      headers: token ? headers(token) : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }

  const setup = await api("/api/v1/private/setup-tokens", undefined, {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", undefined, {
    token: setup.token,
    org_name: "合成跟踪企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const org = `/api/v1/orgs/${admin.org_id}`
  const capacity = `${org}/capacity`
  const adminToken = admin.access_token
  const stamp = crypto.randomUUID()
  const email = (code: string) => `${code}-${stamp}@example.com`
  const roster = await request.post(`${org}/roster/imports`, {
    headers: headers(adminToken),
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          [
            "工号,姓名,部门,上级工号,邮箱",
            `A,合成需求方,客户,,${email("a")}`,
            `M,合成承接经理,研发,,${email("m")}`,
            `W,合成执行员工,研发,M,${email("w")}`,
          ].join("\n"),
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await api(`${org}/roster/imports/${preview.id}/apply`, adminToken, {
    base_revision: preview.base_revision,
  })
  await api(
    `${org}/capacity-settings`,
    adminToken,
    {
      enabled: true,
      requester_policy: "everyone",
    },
    "PUT",
  )
  await api(`${org}/capabilities/versions`, adminToken, {
    expected_version: 0,
    definitions: [
      {
        key: "delivery",
        name: "合成交付",
        category: "工程",
        description: "合成能力",
        level_rubric: {
          levels: [
            { key: "routine", label: "独立完成", description: "合成要求" },
          ],
        },
      },
    ],
  })
  const issued = await request.post(`${org}/accounts/issue`, {
    headers: headers(adminToken),
    data: {},
  })
  expect(issued.ok(), await issued.text()).toBeTruthy()
  const accounts: EmployeeAccount[] = (await api(`${org}/accounts`, adminToken))
    .data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  await api(`${org}/role-grants`, adminToken, {
    user_id: person("M").account!.user_id,
    role: "dept_manager",
    department_id: person("M").department!.id,
  })
  const login = async (code: string) => {
    const reset = await api(
      `${org}/accounts/${person(code).account!.user_id}/reset-password`,
      adminToken,
      {},
    )
    const response = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return (
      await api(
        "/api/v1/users/me/password",
        (
          await response.json()
        ).access_token,
        {
          current_password: reset.initial_password,
          new_password: password,
        },
        "PATCH",
      )
    ).access_token
  }
  const [requester, manager, worker] = [
    await login("A"),
    await login("M"),
    await login("W"),
  ]
  const start = (await api(`${capacity}/workloads/me`, worker)).weeks[0]
    .week_start
  const end = new Date(Date.parse(start) + 6 * 86_400_000)
    .toISOString()
    .slice(0, 10)
  const demand = await api(`${capacity}/demands`, requester, {
    requesting_department_id: person("A").department!.id,
    target_department_id: person("M").department!.id,
    capability_key: "delivery",
    headcount: 1,
    allocation_percent: 50,
    window_start: start,
    window_end: end,
    description: "合成任务跟踪",
  })
  const reply = await api(`${capacity}/demands/${demand.id}/reply`, manager, {
    revision: demand.revision,
    outcome: "can_do",
    start_date: start,
    end_date: end,
    text: "可以承接",
  })
  await api(`${capacity}/demands/${demand.id}/respond`, requester, {
    action: "accept",
    revision: reply.revision,
  })
  await api(
    `${capacity}/weeks`,
    manager,
    {
      items: [{ employee_id: person("W").id, other_percent: 0 }],
      start_week: start,
      end_week: start,
    },
    "PUT",
  )
  await api(`${capacity}/demands/${demand.id}/assignments`, manager, {
    employee_id: person("W").id,
    allocation_percent: 50,
    start_date: start,
    due_date: end,
    outside_basis: true,
  })

  const imported = await request.post(`${org}/sources/csv/imports`, {
    headers: headers(adminToken),
    multipart: {
      file: {
        name: "tasks.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          [
            "记录编号,来源,发生时间,执行人账号,标题,状态",
            `SYN-TRACK,合成工作台,${start}T09:00:00+08:00,${email("w")},完成合成交付,已完成`,
            `SYN-UNKNOWN,合成工作台,${start}T09:00:00+08:00,${email("w")},待核对合成任务,`,
          ].join("\n"),
        ),
      },
    },
  })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  await expect
    .poll(
      async () => (await api(`${org}/sync-runs`, adminToken)).data[0]?.status,
      { timeout: 30_000 },
    )
    .toBe("succeeded")

  const open = async (token: string, search: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, id }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", id)
      },
      { token, id: admin.org_id },
    )
    await page.goto(`/capacity?${search}`)
  }
  await open(worker, "tab=assignments")
  const personal = page.getByRole("region", { name: "我的分配", exact: true })
  await personal.getByText("任务明细", { exact: true }).click()
  await personal.getByLabel("任务编号或标题").fill("SYN-UNKNOWN")
  await personal.getByRole("button", { name: "搜索任务" }).click()
  await personal.getByRole("button", { name: "关联任务", exact: true }).click()
  await expect(personal.getByText("有关联任务状态待确认")).toBeVisible()
  await personal.getByRole("button", { name: "取消关联" }).click()
  await expect(personal.getByText("未关联任务", { exact: true })).toBeVisible()
  await personal.getByLabel("任务编号或标题").fill("SYN-TRACK")
  await personal.getByRole("button", { name: "搜索任务" }).click()
  await personal.getByRole("button", { name: "关联任务", exact: true }).click()
  await expect(
    personal.getByRole("list", { name: "已关联任务" }),
  ).toContainText("SYN-TRACK")
  await expect(
    personal.getByText("关联任务已全部完成，可以结束分配"),
  ).toBeVisible()

  await open(manager, `tab=incoming&demand=${demand.id}`)
  const department = page.getByRole("region", { name: "部门内分配" })
  await expect(
    department.getByText("关联任务已全部完成，可以结束分配"),
  ).toBeVisible()
  await expect(
    department.getByRole("list", { name: "已关联任务" }),
  ).toContainText("SYN-TRACK")

  await open(requester, `tab=outgoing&demand=${demand.id}`)
  const outgoing = page.getByRole("region", { name: "需求详情" })
  const summary = page.getByRole("status").filter({ hasText: "该角色执行跟踪" })
  await expect(summary).toHaveText(
    "该角色执行跟踪：关联任务已全部完成，待承接部门确认结束",
  )
  for (const privateValue of [
    person("W").id,
    "合成执行员工",
    "SYN-TRACK",
    "完成合成交付",
  ]) {
    await expect(outgoing).not.toContainText(privateValue)
    await expect(summary).not.toContainText(privateValue)
  }
})
