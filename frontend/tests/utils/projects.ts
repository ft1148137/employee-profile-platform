import { type APIRequestContext, expect, type Page } from "@playwright/test"
import type { EmployeeAccount } from "../../src/client"

export async function setupProjectManagers(
  request: APIRequestContext,
  validationCategory = "工程",
) {
  async function api(
    url: string,
    data?: object,
    token?: string,
    method = data ? "POST" : "GET",
  ) {
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
  const password = "synthetic-project-password-2026"
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成项目企业",
    admin_username: `projects-${crypto.randomUUID()}@example.com`,
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
            ...[
              ["A", "合成需求经理", "客户"],
              ["B", "合成研发经理", "研发"],
              ["C", "合成测试员工", "测试"],
            ].map(
              (row) => `${row.join(",")},,${crypto.randomUUID()}@example.com`,
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
        ["delivery", "合成交付", "工程"],
        ["validation", "合成验证", validationCategory],
      ].map(([key, name, category]) => ({
        key,
        name,
        category,
        description: "合成能力",
        level_rubric: {
          levels: [
            { key: "routine", label: "独立完成", description: "能独立完成" },
          ],
        },
      })),
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
  const tokens: Record<string, string> = { admin: admin.access_token }
  for (const code of ["A", "B"]) {
    const account = accounts.find((row) => row.employee_code === code)
    if (!account?.account || !account.department)
      throw new Error("缺少合成经理账户")
    await api(
      `${base}/role-grants`,
      {
        user_id: account.account.user_id,
        role: "dept_manager",
        department_id: account.department.id,
      },
      admin.access_token,
    )
    const reset = await api(
      `${base}/accounts/${account.account.user_id}/reset-password`,
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
    tokens[code] = changed.access_token
  }
  await api(
    `${base}/capacity-settings`,
    { enabled: true, requester_policy: "managers" },
    admin.access_token,
    "PUT",
  )
  const departments = Object.fromEntries(
    accounts.flatMap(({ department, employee_code }) =>
      department ? [[employee_code, department.id]] : [],
    ),
  )
  return {
    base,
    adminToken: admin.access_token as string,
    managerToken: tokens.A,
    departments,
    open: async (page: Page, code: string, path: string) => {
      await page.goto("/login")
      await page.evaluate(
        ({ token, orgId }) => {
          localStorage.setItem("access_token", token)
          localStorage.setItem("current_org_id", orgId)
        },
        { token: tokens[code], orgId: admin.org_id },
      )
      await page.goto(path)
    },
  }
}
