import { expect, test } from "@playwright/test"
import type { ProfileItem, ProfileView } from "../src/client"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
test.use({
  viewport: { width: 1600, height: 1000 },
  storageState: { cookies: [], origins: [] },
})

test("demo admin sees all employees, filters, searches and paginates", async ({
  page,
}) => {
  await logInUser(page, "admin@demo.example.com", password)
  await page.goto("/employees")
  await expect(page).toHaveURL(/\/employees\/?$/)
  await expect(page.getByText("共 12 名员工")).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(12)
  await expect(
    page.getByRole("columnheader", { name: "证据质量", exact: true }),
  ).toHaveCount(0)
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "张伟" })
      .filter({ hasText: "平台研发部" }),
  ).toContainText("待本人确认")
  await expect(
    page.getByRole("columnheader", { name: "账号状态", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("columnheader", { name: "角色", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: "人员", exact: true }),
  ).toHaveCount(0)
  await page.getByRole("button", { name: "档案字段", exact: true }).click()
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: "档案字段", exact: true }),
  ).toBeVisible()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .click()
  await expect(page.getByRole("dialog")).toBeHidden()
  await page.screenshot({
    path: "../docs/screenshots/m4.2/employees-admin.png",
    fullPage: true,
  })
  await page.getByRole("combobox").last().click()
  await page.getByRole("option", { name: "5", exact: true }).click()
  await expect(page.locator("tbody tr")).toHaveCount(5)
  await page.getByRole("button", { name: "下一页", exact: true }).click()
  await expect(page.getByText("第 6–10 条，共 12 条")).toBeVisible()
  await page.getByRole("combobox", { name: "部门筛选" }).click()
  await page.getByRole("option", { name: "平台研发部", exact: true }).click()
  await expect(page.getByText("共 3 名员工")).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(3)
  await expect(
    page.getByRole("columnheader", { name: "证据质量", exact: true }),
  ).toHaveCount(0)
  await page.getByRole("textbox", { name: "搜索姓名或工号" }).fill("林澈")
  await expect(page.locator("tbody tr")).toHaveCount(1)
  await page
    .getByRole("row")
    .getByRole("cell", { name: "E01", exact: true })
    .click()
  const preview = page.getByRole("dialog", { name: "员工快速预览" })
  await expect(preview).toContainText("林澈")
  await expect(page).toHaveURL(/\/employees\/?$/)
  await preview.getByRole("link", { name: "进入完整画像" }).click()
  await expect(
    page.getByRole("heading", { name: "林澈", exact: true }),
  ).toBeVisible()
})

test("platform manager sees only their department", async ({ page }) => {
  await logInUser(page, "manager.platform@demo.example.com", password)
  const profileRequests: string[] = []
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname
    if (path.endsWith("/profile")) profileRequests.push(path)
  })
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.getByRole("link", { name: "我的团队", exact: true }).click()
  await expect(page.getByText("共 3 名员工")).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(3)
  await expect(
    page.locator("tbody tr").filter({ hasText: "平台研发部" }),
  ).toHaveCount(3)
  await expect(
    page.getByRole("columnheader", { name: "账号状态", exact: true }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("columnheader", { name: "角色", exact: true }),
  ).toHaveCount(0)
  await page.screenshot({
    path: "test-results/employees-manager.png",
    fullPage: true,
  })
  const departmentFilter = page.getByRole("combobox", { name: "部门筛选" })
  await departmentFilter.click()
  await expect(page.getByRole("option")).toHaveCount(2)
  await expect(page.getByRole("option", { name: "算法与模型部" })).toHaveCount(
    0,
  )
  await page.keyboard.press("Escape")
  await expect(departmentFilter).toBeFocused()
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "E02", exact: true }) })
  const detailLink = row.getByRole("link", { name: "张伟", exact: true })
  const profilePath = `${await detailLink.getAttribute("href")}/profile`
  const subjectRequests = () =>
    profileRequests.filter((path) => path.endsWith(profilePath))
  await page.route(
    `**${profilePath}`,
    async (route) => {
      const response = await route.fetch()
      const profile: ProfileView = await response.json()
      const published = profile.items.find(
        ({ capability }) => capability.name === "后端 API 开发",
      )
      if (!published?.claim) throw new Error("Expected the demo backend claim")
      const current: ProfileItem = {
        ...published,
        capability: {
          ...published.capability,
          level_rubric: { levels: [{ key: "L2", label: "可独立完成" }] },
        },
        status: "confirmed",
        claim: { ...published.claim, level: "L2" },
      }
      const items: ProfileItem[] = [
        {
          ...current,
          capability: {
            ...current.capability,
            id: "retired",
            name: "历史能力",
          },
          retired: true,
        },
        {
          ...current,
          claim: { ...published.claim, level: "L3" },
          level_adjusted: true,
        },
        {
          ...current,
          capability: {
            ...current.capability,
            id: "ungraded",
            name: "测试设计",
          },
          claim: { ...published.claim, level: null },
        },
        {
          ...current,
          capability: {
            ...current.capability,
            id: "disputed",
            name: "故障排查",
          },
          status: "disputed",
        },
      ]
      await route.fulfill({ response, json: { ...profile, items } })
    },
    { times: 1 },
  )
  expect(subjectRequests()).toHaveLength(0)
  await row.focus()
  await row.press("Enter")
  const preview = page.getByRole("dialog", { name: "员工快速预览" })
  await expect(preview).toContainText("张伟")
  const capabilities = preview.getByRole("listitem")
  const adjusted = capabilities.filter({ hasText: "后端 API 开发" })
  await expect(adjusted).toContainText("等级已调整")
  await expect(adjusted.getByText("L3", { exact: true })).toHaveCount(0)
  const ungraded = capabilities.filter({ hasText: "测试设计" })
  await expect(ungraded).toContainText("未定级")
  await expect(ungraded.getByText("已确认", { exact: true })).toHaveCount(0)
  const disputed = capabilities.filter({ hasText: "故障排查" })
  await expect(disputed).toContainText("可独立完成")
  await expect(disputed).toContainText("存在争议")
  await expect(capabilities).toHaveCount(3)
  await expect(preview.getByText("历史能力", { exact: true })).toHaveCount(0)
  expect(subjectRequests()).toHaveLength(1)
  await page.screenshot({
    path: test.info().outputPath("employee-quick-peek.png"),
    animations: "disabled",
  })
  await page.keyboard.press("Escape")
  await expect(preview).toBeHidden()
  await expect(row).toBeFocused()
  await detailLink.click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
})

for (const status of [403, 404]) {
  test(`quick preview handles profile response ${status} without showing capabilities`, async ({
    page,
  }) => {
    await logInUser(page, "manager.platform@demo.example.com", password)
    await page.route("**/employees/*/profile", (route) =>
      route.fulfill({ status, json: { detail: "无法查看" } }),
    )
    await page.goto("/employees")
    await page.getByRole("cell", { name: "E02", exact: true }).click()
    const preview = page.getByRole("dialog", { name: "员工快速预览" })
    await expect(preview).toContainText("张伟")
    await expect(preview.getByRole("alert")).toContainText("没有权限查看画像")
    await expect(preview.getByText("后端 API 开发")).toHaveCount(0)
    await expect(
      preview.getByRole("link", { name: "进入完整画像" }),
    ).toHaveCount(0)
  })
}

for (const [email, cardsLabel] of [
  ["admin@demo.example.com", "员工卡片"],
  ["manager.platform@demo.example.com", "团队员工卡片"],
]) {
  test(`mobile employee cards fit the viewport for ${email}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await logInUser(page, email, password)
    await page.goto("/employees")
    const cards = page.getByRole("region", { name: cardsLabel })
    const link = cards.getByRole("link", { name: "林澈", exact: true })
    await expect(link).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(375)
    await page.setViewportSize({ width: 390, height: 844 })
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(390)
    const card = cards.getByRole("article").filter({
      has: page.getByRole("link", { name: "林澈", exact: true }),
    })
    await card.getByRole("button", { name: "快速查看", exact: true }).click()
    const preview = page.getByRole("dialog", { name: "员工快速预览" })
    await expect(preview).toContainText("林澈")
    await preview.getByRole("button", { name: "关闭", exact: true }).click()
    await expect(preview).toBeHidden()
    await expect(page).toHaveURL(/\/employees$/)
    await link.click()
    await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  })
}

test("employee lands on own profile and cannot read a colleague", async ({
  page,
  request,
}) => {
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: "manager.platform@demo.example.com", password },
  })
  const { access_token } = await login.json()
  const memberships = await request.get("/api/v1/me/memberships", {
    headers: { Authorization: `Bearer ${access_token}` },
  })
  const [manager] = await memberships.json()
  await logInUser(page, "employee.platform@demo.example.com", password)
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: "员工", exact: true }),
  ).toHaveCount(0)
  await page.goto(`/employees/${manager.employee_id}`)
  await expect(page.getByText("未找到该员工或你没有权限查看")).toBeVisible()
})

test("user without memberships sees the empty organization state", async ({
  page,
}) => {
  const email = randomEmail()
  const userPassword = randomPassword()
  await createUser({ email, password: userPassword })
  await logInUser(page, email, userPassword)
  await expect(
    page.getByText("你的账号尚未加入任何企业，请联系企业管理员。"),
  ).toBeVisible()
  await page.screenshot({ path: "test-results/empty-org.png", fullPage: true })
})

test("invalid saved organization falls back to a membership", async ({
  page,
}) => {
  await page.goto("/login")
  await page.evaluate(() =>
    localStorage.setItem("current_org_id", "removed-org"),
  )
  await logInUser(page, "admin@demo.example.com", password)
  await page.goto("/employees")
  await expect(page.getByText("共 12 名员工")).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("current_org_id")))
    .not.toBe("removed-org")
})

test("organization switch updates navigation and scopes requests", async ({
  page,
}) => {
  const orgs = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
  ]
  await page.route("**/api/v1/me/memberships", (route) =>
    route.fulfill({
      json: orgs.map((id, i) => ({
        org: { id, name: `测试组织 ${i + 1}`, slug: `test-${i}` },
        roles: i === 0 ? ["org_admin"] : ["employee"],
        employee_id: i === 0 ? null : "own-employee",
        managed_department_ids: [],
      })),
    }),
  )
  await page.route("**/api/v1/orgs/*/departments", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/orgs/*/{employees,accounts}?*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route(`**/api/v1/orgs/${orgs[1]}/me/inbox?*`, (route) =>
    route.fulfill({
      json: { data: [], count: 0, unread_count: 0, pending_count: 0 },
    }),
  )
  await page.route("**/api/v1/orgs/*/employees/own-employee/profile", (route) =>
    route.fulfill({ status: 404, json: { detail: "Employee not found" } }),
  )
  await logInUser(page, "admin@demo.example.com", password)
  await page.goto("/employees")
  await expect(page.getByText("共 0 名员工")).toBeVisible()
  await page.getByRole("combobox", { name: "切换企业" }).click()
  await page.getByRole("option", { name: "测试组织 2" }).click()
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/own-employee$/)
  await expect(page.getByText("未找到该员工或你没有权限查看")).toBeVisible()
  await expect(
    page.getByRole("link", { name: "员工", exact: true }),
  ).toHaveCount(0)
  await page.reload()
  await expect(
    page.getByRole("combobox", { name: "切换企业" }),
  ).toHaveAttribute("title", "测试组织 2")
})

test("employee list handles failure, retry and empty search", async ({
  page,
}) => {
  await page.route("**/api/v1/orgs/*/employees?*", (route) =>
    route.fulfill({ status: 500, json: { detail: "Temporary failure" } }),
  )
  await logInUser(page, "manager.platform@demo.example.com", password)
  await page.goto("/employees")
  await expect(page.getByText("无法加载员工列表。")).toBeVisible({
    timeout: 15000,
  })
  await page.unroute("**/api/v1/orgs/*/employees?*")
  await page.getByRole("button", { name: "重试" }).click()
  await expect(page.getByText("共 3 名员工")).toBeVisible()
  await page.getByRole("textbox", { name: "搜索姓名" }).fill("不存在的姓名")
  await expect(page.getByText("暂无数据")).toBeVisible()
})

test("setup administrator appears only in account-only mode without a detail link", async ({
  page,
  request,
}) => {
  const issued = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const email = `unlinked-${crypto.randomUUID()}@example.com`
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await issued.json()).token,
      org_name: "合成纯账号企业",
      admin_username: email,
      admin_name: "合成管理员",
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  await page.goto("/login")
  await page.evaluate(
    ({ access_token, org_id }) => {
      localStorage.setItem("access_token", access_token)
      localStorage.setItem("current_org_id", org_id)
    },
    { access_token, org_id },
  )
  await page.goto("/employees?filter=account-only")
  const row = page.getByRole("row").filter({ hasText: email })
  await expect(row).toContainText("企业管理员")
  await expect(row.getByRole("link")).toHaveCount(0)
  await row.getByRole("cell").first().click()
  await expect(page).toHaveURL("/employees?filter=account-only")
  await row.getByRole("button", { name: "更多", exact: true }).click()
  await expect(page.getByRole("menu")).toContainText(
    "请在「设置 → 账号与安全」修改本人密码",
  )
  await expect(
    page.getByRole("menu").getByRole("menuitem", { name: "发放账号" }),
  ).toHaveCount(0)
})

test("unmatched Jira dialog links an account to the selected employee", async ({
  page,
  request,
}) => {
  await logInUser(page, "admin@demo.example.com", password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await request.get("/api/v1/me/memberships", { headers })
  const base = `/api/v1/orgs/${(await memberships.json())[0].org.id}`
  const response = await request.get(
    `${base}/external-identities?source=jira&status=linked`,
    { headers },
  )
  const identity = (await response.json()).data[0]
  expect(identity.employee).toBeTruthy()
  expect(
    (
      await request.post(`${base}/external-identities/${identity.id}/unlink`, {
        headers,
      })
    ).ok(),
  ).toBeTruthy()
  await page.goto("/employees")
  await page.getByRole("button", { name: /\d+ 个 Jira 账号未匹配/ }).click()
  const unmatched = page.getByRole("dialog", {
    name: "未匹配 Jira 账号",
    exact: true,
  })
  await unmatched
    .getByRole("row")
    .filter({ hasText: identity.external_id })
    .getByRole("button", { name: "选择员工", exact: true })
    .click()
  const picker = page.getByRole("dialog", { name: "选择员工", exact: true })
  await picker
    .getByLabel("搜索员工姓名或工号")
    .fill(identity.employee.employee_code)
  await picker.getByRole("button", { name: /在职/ }).click()
  await expect(picker).toBeHidden()
  await expect(unmatched.getByRole("status")).toContainText("已关联 1 个账号")
  await expect(
    unmatched.getByRole("row").filter({ hasText: identity.external_id }),
  ).toHaveCount(0)
  const linked = await request.get(
    `${base}/external-identities?source=jira&status=linked`,
    { headers },
  )
  expect(
    (await linked.json()).data.find(
      (row: { id: string }) => row.id === identity.id,
    ).employee.id,
  ).toBe(identity.employee.id)
})
