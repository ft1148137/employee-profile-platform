import { expect, test } from "@playwright/test"
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
    page
      .getByRole("row")
      .filter({ hasText: "张伟" })
      .filter({ hasText: "平台研发部" }),
  ).toContainText("待本人确认")
  await page.screenshot({
    path: "test-results/employees-admin.png",
    fullPage: true,
  })
  await page.getByRole("combobox").last().click()
  await page.getByRole("option", { name: "5", exact: true }).click()
  await expect(page.locator("tbody tr")).toHaveCount(5)
  await page.getByRole("button", { name: "下一页", exact: true }).click()
  await expect(page.getByText("显示第 6 至 10 条，共 12 条")).toBeVisible()
  await page.getByRole("combobox", { name: "部门筛选" }).click()
  await page.getByRole("option", { name: "平台研发部", exact: true }).click()
  await expect(page.getByText("共 3 名员工")).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(3)
  await page.getByRole("textbox", { name: "搜索姓名" }).fill("林澈")
  await expect(page.locator("tbody tr")).toHaveCount(1)
  await page.getByRole("row").filter({ hasText: "林澈" }).click()
  await expect(page.getByRole("heading", { name: "林澈" })).toBeVisible()
})

test("platform manager sees only their department", async ({ page }) => {
  await logInUser(page, "manager.platform@demo.example.com", password)
  await expect(page.getByText("共 3 名员工")).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(3)
  await expect(
    page.locator("tbody tr").filter({ hasText: "平台研发部" }),
  ).toHaveCount(3)
  await page.screenshot({
    path: "test-results/employees-manager.png",
    fullPage: true,
  })
  await page.getByRole("combobox", { name: "部门筛选" }).click()
  await expect(page.getByRole("option")).toHaveCount(2)
  await expect(page.getByRole("option", { name: "算法与模型部" })).toHaveCount(
    0,
  )
})

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
    page.getByText("你还没有加入任何组织，请联系管理员"),
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
  await page.route("**/api/v1/orgs/*/employees?*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/orgs/*/employees/own-employee/profile", (route) =>
    route.fulfill({ status: 404, json: { detail: "Employee not found" } }),
  )
  await logInUser(page, "admin@demo.example.com", password)
  await page.goto("/employees")
  await expect(page.getByText("共 0 名员工")).toBeVisible()
  await page.getByRole("combobox", { name: "当前组织" }).click()
  await page.getByRole("option", { name: "测试组织 2" }).click()
  await expect(page).toHaveURL(/\/employees\/own-employee$/)
  await expect(page.getByText("未找到该员工或你没有权限查看")).toBeVisible()
  await expect(
    page.getByRole("link", { name: "员工", exact: true }),
  ).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole("combobox", { name: "当前组织" })).toHaveText(
    "测试组织 2",
  )
})

test("employee list handles failure, retry and empty search", async ({
  page,
}) => {
  await page.route("**/api/v1/orgs/*/employees?*", (route) =>
    route.fulfill({ status: 500, json: { detail: "Temporary failure" } }),
  )
  await logInUser(page, "admin@demo.example.com", password)
  await page.goto("/employees")
  await expect(page.getByText("无法加载员工列表。")).toBeVisible({
    timeout: 15000,
  })
  await page.unroute("**/api/v1/orgs/*/employees?*")
  await page.getByRole("button", { name: "重试" }).click()
  await expect(page.getByText("共 12 名员工")).toBeVisible()
  await page.getByRole("textbox", { name: "搜索姓名" }).fill("不存在的姓名")
  await expect(page.getByText("暂无数据。")).toBeVisible()
})
