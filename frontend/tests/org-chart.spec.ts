import { expect, test } from "@playwright/test"
import type { ProfileView } from "../src/client"
import { logInUser } from "./utils/user"

test.use({
  viewport: { width: 1440, height: 900 },
  storageState: { cookies: [], origins: [] },
})

const syntheticChart = {
  departments: [
    { id: "root", name: "研发中心", parent_department_id: null },
    ...["平台部", "产品部", "数据部", "交付部"].map((name, i) => ({
      id: `dept-${i}`,
      name,
      parent_department_id: "root",
    })),
    ...["质量组", "设计组", "分析组", "支持组"].map((name, i) => ({
      id: `team-${i}`,
      name,
      parent_department_id: `dept-${i}`,
    })),
  ].map((department) => ({
    ...department,
    managers: [{ employee_id: "lead", display_name: "林清" }],
    direct_count:
      department.id === "root" ? 1 : department.id === "dept-0" ? 3 : 0,
    total_count:
      department.id === "root" ? 4 : department.id === "dept-0" ? 3 : 0,
  })),
  people: [
    {
      id: "director",
      display_name: "陈远",
      title: "研发负责人",
      department_id: "root",
      manager_id: null,
    },
    {
      id: "lead",
      display_name: "林清",
      title: "平台经理",
      department_id: "dept-0",
      manager_id: "director",
    },
    {
      id: "engineer",
      display_name: "周宁",
      title: "后端工程师",
      department_id: "dept-0",
      manager_id: "lead",
    },
    {
      id: "report",
      display_name: "许言",
      title: "开发工程师",
      department_id: "dept-0",
      manager_id: "engineer",
    },
  ],
}

test("admin opens department reporting tree, person chain and employee details", async ({
  page,
}, testInfo) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/employees")
  await expect(
    page.getByRole("heading", { name: "员工", exact: true }),
  ).toBeVisible()
  await page
    .getByRole("navigation", { name: "主导航", exact: true })
    .getByRole("link", { name: "组织架构", exact: true })
    .click()
  await expect(page).toHaveURL(/\/org-chart$/)
  const tree = page.getByRole("region", { name: "部门层级", exact: true })
  await expect(
    page.getByText("4 个可见部门 · 人数仅统计在职员工"),
  ).toBeVisible()
  await expect(tree).toContainText("平台研发部")
  await page.screenshot({
    path: testInfo.outputPath("org-chart-admin.png"),
    fullPage: true,
  })
  await tree.getByRole("button", { name: "查看平台研发部人员" }).click()
  const people = page.getByRole("region", { name: "部门汇报关系" })
  await expect(people).toContainText("共 3 人")
  await people.getByRole("button", { name: "查看张伟汇报关系" }).click()
  await expect(
    page.getByRole("region", { name: "人员汇报关系" }),
  ).toContainText("张伟 · 上级与直接下属")
  await page.getByRole("link", { name: "查看详情", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("three department levels have connectors, fit viewport and collapse", async ({
  page,
}, testInfo) => {
  await page.route("**/api/v1/orgs/*/org-chart", (route) =>
    route.fulfill({ json: syntheticChart }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/org-chart")
  await expect(page.getByTestId("org-connector")).toHaveCount(5)
  for (const name of ["研发中心", "平台部", "质量组", "支持组"]) {
    await expect(
      page.getByRole("button", { name: `查看${name}人员` }),
    ).toBeInViewport()
  }
  const overflow = await page
    .getByRole("region", { name: "可滚动组织架构图" })
    .evaluate((el) => ({
      horizontal: el.scrollWidth > el.clientWidth,
      vertical: el.scrollHeight > el.clientHeight,
    }))
  expect(overflow).toEqual({ horizontal: false, vertical: false })
  await page.screenshot({
    path: testInfo.outputPath("org-chart-hierarchy.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "折叠平台部下级部门" }).click()
  await expect(
    page.getByRole("button", { name: "查看质量组人员" }),
  ).toBeHidden()
  await page.getByRole("button", { name: "展开平台部下级部门" }).click()
  await page.getByRole("button", { name: "折叠研发中心下级部门" }).click()
  await expect(
    page.getByRole("button", { name: "查看平台部人员" }),
  ).toBeHidden()
  await page.getByRole("button", { name: "展开研发中心下级部门" }).click()
  await expect(
    page.getByRole("button", { name: "查看质量组人员" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "查看质量组人员" }).click()
  await expect(page.getByText("本部门暂无在职人员")).toBeVisible()
  await page.getByRole("button", { name: "部门层级", exact: true }).click()
  await page.getByRole("button", { name: "查看平台部人员" }).click()
  await expect(page.getByTestId("org-connector")).toHaveCount(2)
  await page.screenshot({
    path: testInfo.outputPath("org-chart-department.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "查看周宁汇报关系" }).click()
  const personTree = page.getByRole("region", { name: "人员汇报关系" })
  for (const name of ["陈远", "林清", "周宁", "许言"])
    await expect(
      personTree.getByRole("button", { name: `查看${name}汇报关系` }),
    ).toBeVisible()
  await expect(
    personTree.getByRole("button", { name: "查看周宁汇报关系" }),
  ).toHaveAttribute("aria-pressed", "true")
  await page.screenshot({
    path: testInfo.outputPath("org-chart-person.png"),
    fullPage: true,
  })
  await personTree.getByRole("button", { name: "查看林清汇报关系" }).click()
  await expect(
    personTree.getByRole("button", { name: "查看林清汇报关系" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    personTree.getByRole("button", { name: "查看许言汇报关系" }),
  ).toBeHidden()
  await page.getByRole("button", { name: "平台部", exact: true }).click()
  await expect(page.getByRole("region", { name: "部门汇报关系" })).toBeVisible()
  await page.evaluate(() => localStorage.setItem("vite-ui-theme", "light"))
  await page.reload()
  await expect(page.locator("html")).toHaveClass("light")
  await expect(page.getByTestId("org-connector")).toHaveCount(5)
  await page.screenshot({
    path: testInfo.outputPath("org-chart-light.png"),
    fullPage: true,
  })
})

test("reporting cycles become marked roots and person navigation terminates", async ({
  page,
}) => {
  await page.route("**/api/v1/orgs/*/org-chart", (route) =>
    route.fulfill({
      json: {
        ...syntheticChart,
        people: syntheticChart.people.map((person) =>
          person.id === "lead" ? { ...person, manager_id: "engineer" } : person,
        ),
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/org-chart")
  await page.getByRole("button", { name: "查看平台部人员" }).click()
  await expect(page.getByText("汇报线有循环", { exact: true })).toHaveCount(2)
  await expect(page.locator(".org-tree > li")).toHaveCount(2)
  await page.getByRole("button", { name: "查看林清汇报关系" }).click()
  await expect(
    page.getByRole("button", { name: "查看周宁汇报关系" }),
  ).toHaveCount(1)
  await expect(page.getByRole("link", { name: "查看详情" })).toBeVisible()
})

test("manager chart excludes other departments", async ({ page }, testInfo) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/employees")
  await expect(page).toHaveURL(/\/employees\/?$/)
  await expect(
    page.getByRole("heading", { name: "员工", exact: true }),
  ).toBeVisible()
  await page
    .getByRole("navigation", { name: "主导航", exact: true })
    .getByRole("link", { name: "组织架构", exact: true })
    .click()
  await expect(
    page.getByText("1 个可见部门 · 人数仅统计在职员工"),
  ).toBeVisible()
  const tree = page.getByRole("region", { name: "部门层级", exact: true })
  for (const name of ["算法与模型部", "交付与技术支持部", "边缘计算部"])
    await expect(tree).not.toContainText(name)
  await tree.getByRole("button", { name: "查看平台研发部人员" }).click()
  await expect(
    page.getByRole("region", { name: "部门汇报关系" }),
  ).toContainText("共 3 人")
  const capabilities = page.getByRole("region", {
    name: "对应能力",
    exact: true,
  })
  await expect(capabilities).toBeVisible()
  await expect(capabilities.getByRole("button")).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath("org-chart-manager.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "查看张伟汇报关系" }).click()
  await page.getByRole("link", { name: "查看详情", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("employees cannot navigate to or open org chart", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(
    page
      .getByRole("navigation", { name: "主导航", exact: true })
      .getByRole("link", { name: "组织架构", exact: true }),
  ).toHaveCount(0)
  await page.goto("/org-chart")
  await expect(page.getByRole("alert")).toHaveText(
    "需要企业管理员或部门经理权限",
  )
})

for (const legacy of [
  "/employees?view=org-chart",
  "/admin/people?tab=org-chart",
])
  test(`legacy org chart URL redirects: ${legacy}`, async ({ page }) => {
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.goto(legacy)
    await expect(page).toHaveURL(/\/org-chart$/)
    await expect(
      page.getByRole("heading", { name: "组织架构", exact: true }),
    ).toBeVisible()
  })

test("administrator saves department capabilities and the profile follows the selection", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const base = `/api/v1/orgs/${memberships[0].org.id}`
  const employees = await (
    await request.get(`${base}/employees?q=张伟`, { headers })
  ).json()
  const employee = employees.data[0]
  const endpoint = `${base}/departments/${employee.department.id}/capabilities`
  const original = await (await request.get(endpoint, { headers })).json()
  try {
    await page.goto("/org-chart")
    await page.getByRole("button", { name: "查看平台研发部人员" }).click()
    const panel = page.getByRole("region", { name: "对应能力", exact: true })
    for (const name of ["前端交互实现", "模型训练与评估"]) {
      await panel.getByRole("button", { name: "添加能力", exact: true }).click()
      const picker = page.getByRole("dialog", { name: "添加能力" })
      await picker.getByLabel("搜索能力").fill(name)
      await picker.getByRole("button", { name, exact: true }).click()
    }
    await panel.getByRole("button", { name: "保存对应能力" }).click()
    await expect(panel.getByRole("status")).toHaveText("对应能力已保存")
    await page.screenshot({
      path: "../docs/screenshots/m4.2/org-chart-department-capabilities.png",
      fullPage: true,
    })
    const profile: ProfileView = await (
      await request.get(`${base}/employees/${employee.id}/profile`, { headers })
    ).json()
    expect(
      profile.items
        .filter((item) => item.in_department)
        .map((item) => item.capability.key)
        .sort(),
    ).toEqual(["frontend_ui", "model_training"])
    await page.goto(`/employees/${employee.id}`)
    for (const name of ["前端交互实现", "模型训练与评估"])
      await expect(
        page.getByRole("article", { name, exact: true }),
      ).toBeVisible()
    await expect(
      page.getByRole("article", { name: "后端 API 开发", exact: true }),
    ).toContainText("其他部门能力")
    await page.goto("/org-chart")
    await page.getByRole("button", { name: "查看平台研发部人员" }).click()
    await panel.getByRole("button", { name: "移除前端交互实现" }).click()
    await panel.getByRole("button", { name: "保存对应能力" }).click()
    await expect(panel.getByRole("status")).toHaveText("对应能力已保存")
    await page.goto(`/employees/${employee.id}`)
    await expect(
      page.getByRole("article", { name: "前端交互实现", exact: true }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("article", { name: "模型训练与评估", exact: true }),
    ).toBeVisible()
  } finally {
    expect(
      (
        await request.put(endpoint, {
          headers,
          data: { capability_keys: original.capability_keys },
        })
      ).ok(),
    ).toBeTruthy()
  }
})

test("inherited capabilities stay read only when an administrator removes a local assignment", async ({
  page,
}) => {
  await page.route("**/api/v1/orgs/*/org-chart", (route) =>
    route.fulfill({ json: syntheticChart }),
  )
  await page.route("**/departments/dept-0/capabilities", (route) =>
    route.fulfill({
      json: {
        capability_keys: ["backend_api"],
        inherited_capability_keys: ["backend_api"],
        capabilities: [
          { key: "backend_api", name: "后端 API 开发", category: "工程开发" },
        ],
      },
    }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/org-chart")
  await page.getByRole("button", { name: "查看平台部人员" }).click()
  const panel = page.getByRole("region", { name: "对应能力", exact: true })
  await expect(panel).toContainText("后端 API 开发 · 继承自上级部门")
  await panel.getByRole("button", { name: "移除后端 API 开发" }).click()
  await expect(panel).toContainText("暂无本部门设置")
  await expect(panel).toContainText("后端 API 开发 · 继承自上级部门")
  await expect(
    panel.getByRole("button", { name: "移除后端 API 开发" }),
  ).toHaveCount(0)
})
