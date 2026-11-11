import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({
  viewport: { width: 1440, height: 1000 },
  storageState: { cookies: [], origins: [] },
})

test("admin selects a department and opens personnel details", async ({
  page,
}, testInfo) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/people?tab=org-chart")
  const tree = page.getByRole("region", { name: "部门层级" })
  await expect(tree).toContainText("4 个可见部门")
  await tree.getByRole("button", { name: "查看平台研发部人员" }).click()
  const people = page.getByRole("complementary", { name: "部门人员" })
  await expect(people).toContainText("共 3 人")
  await expect(
    people.getByRole("link", { name: "张伟", exact: true }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("org-chart-admin.png"),
    fullPage: true,
  })
  await people.getByRole("link", { name: "张伟", exact: true }).click()
  await expect(page).toHaveURL(/\/admin\/people\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("manager chart excludes other departments and opens employee details", async ({
  page,
}, testInfo) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/employees")
  await page.getByRole("tab", { name: "组织架构", exact: true }).click()
  const tree = page.getByRole("region", { name: "部门层级" })
  await expect(tree).toContainText("1 个可见部门")
  await expect(tree).not.toContainText("算法与模型部")
  await expect(tree).not.toContainText("交付与技术支持部")
  await expect(tree).not.toContainText("边缘计算部")
  await tree.getByRole("button", { name: "查看平台研发部人员" }).click()
  const people = page.getByRole("complementary", { name: "部门人员" })
  await expect(people).toContainText("共 3 人")
  await page.screenshot({
    path: testInfo.outputPath("org-chart-manager.png"),
    fullPage: true,
  })
  await people.getByRole("link", { name: "张伟", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("hierarchy expands two levels, collapses, and reports empty departments", async ({
  page,
}, testInfo) => {
  await page.route("**/api/v1/orgs/*/org-chart", (route) =>
    route.fulfill({
      json: {
        departments: ["研发中心", "平台部", "质量组"].map((name, index) => ({
          id: String(index),
          name,
          parent_department_id: index ? String(index - 1) : null,
          managers: [],
          direct_count: 0,
          total_count: 0,
        })),
      },
    }),
  )
  await page.route("**/api/v1/orgs/*/employees?*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/people?tab=org-chart")
  await expect(
    page.getByRole("button", { name: "查看平台部人员" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "查看质量组人员" }),
  ).toBeHidden()
  await page.getByRole("button", { name: "展开平台部下级部门" }).click()
  await page.getByRole("button", { name: "查看质量组人员" }).click()
  await expect(page.getByText("本部门暂无在职人员")).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("org-chart-hierarchy.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "折叠研发中心下级部门" }).click()
  await expect(
    page.getByRole("button", { name: "查看平台部人员" }),
  ).toBeHidden()
  await page.getByRole("button", { name: "展开研发中心下级部门" }).click()
  await expect(
    page.getByRole("button", { name: "查看质量组人员" }),
  ).toBeVisible()
})
