import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

async function setupProjectManagers(request: APIRequestContext) {
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
        ["delivery", "合成交付"],
        ["validation", "合成验证"],
      ].map(([key, name]) => ({
        key,
        name,
        category: "工程",
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
  const tokens: Record<string, string> = {}
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
  return {
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

test("需求方创建多角色项目并看到承接方答复和排程影响", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const { open } = await setupProjectManagers(request)
  await open(page, "A", "/capacity?tab=projects")
  await page.getByRole("button", { name: "新建项目需求" }).click()
  await page.getByLabel("项目名称").fill("合成跨部门交付")
  await page.getByLabel("需求方部门").selectOption({ label: "客户" })
  const addRole = page.getByRole("button", { name: "添加角色", exact: true })
  const roles = [
    ["技术验证", "测试", "2026-12-01", "2026-12-02"],
    ["接口开发", "研发", "2026-12-03", "2026-12-04"],
    ["上线验收", "测试", "2026-12-05", "2026-12-06"],
  ] as const
  for (const [index, [name, department, start, end]] of roles.entries()) {
    if (index) await addRole.click()
    const role = page.getByRole("group", {
      name: `角色 ${index + 1}`,
      exact: true,
    })
    await role.getByLabel("角色名称").fill(name)
    await role.getByLabel("目标部门").selectOption({ label: department })
    await role.getByLabel("主能力").selectOption({ label: "合成交付" })
    await role.getByLabel("人数").fill("1")
    await role.getByLabel("每人投入比例（%）").fill("50")
    await role.getByLabel("时间窗口开始").fill(start)
    await role.getByLabel("时间窗口结束").fill(end)
    await role.getByLabel("需求说明").fill(`交付要求：${name}`)
    if (index)
      await role.getByRole("checkbox", { name: roles[index - 1][0] }).check()
    if (index === 0) {
      await role.getByRole("button", { name: "添加额外能力" }).click()
      await role
        .getByLabel("额外能力", { exact: true })
        .selectOption({ label: "合成验证" })
      await role.getByRole("checkbox", { name: "必需（不勾选为可选）" }).check()
    }
  }

  await page.getByRole("button", { name: "提交项目需求" }).click()
  await expect(
    page.getByRole("heading", { name: "合成跨部门交付" }),
  ).toBeVisible()
  const projectPath = new URL(page.url()).pathname + new URL(page.url()).search
  await expect(page.getByRole("table", { name: "第 3 阶段" })).toContainText(
    "2026-12-05 至 2026-12-06",
  )
  await page.getByRole("tab", { name: "我提的需求" }).click()
  await expect(
    page.getByText("项目：合成跨部门交付 · 角色：接口开发"),
  ).toBeVisible()

  await open(page, "B", "/capacity?tab=incoming")
  const incoming = page.locator("article").filter({ hasText: "角色：接口开发" })
  await incoming.getByRole("button", { name: "查看详情" }).click()
  await expect(page.getByRole("list", { name: "前置角色" })).toContainText(
    "技术验证（测试）",
  )
  await page.getByLabel("答复结论").selectOption({ label: "能做" })
  await page.getByLabel("最早开始日期").fill("2026-12-03")
  await page.getByLabel("承诺完成日期").fill("2026-12-06")
  await page.getByLabel("答复说明").fill("需要增加两个工作日完成开发")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "已答复",
  )
  await page.getByRole("link", { name: "合成跨部门交付" }).click()
  await expect(
    page.getByRole("region", { name: "项目详情" }),
  ).not.toContainText("交付要求：技术验证")

  await open(page, "A", projectPath)
  const details = page.getByRole("region", { name: "项目详情" })
  await expect(
    details.getByRole("listitem").filter({ hasText: "接口开发" }),
  ).toContainText("已答复 · 能做")
  await expect(details).not.toContainText("合成研发经理")
  await expect(page.getByRole("table", { name: "第 3 阶段" })).toContainText(
    "2026-12-07 至 2026-12-08",
  )
  await expect(page.getByRole("region", { name: "项目影响" })).toContainText(
    "预计比需求窗口晚 2 天",
  )
})
