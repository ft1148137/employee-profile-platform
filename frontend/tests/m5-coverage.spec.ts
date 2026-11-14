import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({
  viewport: { width: 1600, height: 1000 },
  storageState: { cookies: [], origins: [] },
})

for (const role of [
  {
    email: "admin@demo.example.com",
    heading: "员工",
    total: 12,
    filtered: 3,
    endpoint: "accounts",
  },
  {
    email: "manager.platform@demo.example.com",
    heading: "我的团队",
    total: 3,
    filtered: 3,
    endpoint: "employees",
  },
]) {
  test(`${role.heading} shows scoped coverage and filters the list by department`, async ({
    page,
  }) => {
    await logInUser(page, role.email, process.env.DEMO_USER_PASSWORD!)
    await page.goto("/employees")
    await expect(
      page.getByRole("heading", { name: role.heading, exact: true }),
    ).toBeVisible()
    const coverage = page.getByRole("region", { name: "画像覆盖率" })
    await expect(
      coverage.getByText(`范围内在职员工 ${role.total} 人`),
    ).toBeVisible()
    for (const status of [
      "已发布",
      "存在争议",
      "待本人过目",
      "待审批",
      "未知",
    ]) {
      await expect(
        coverage.getByText(status, { exact: true }).first(),
      ).toBeVisible()
    }
    await expect(coverage.getByText(/等待审批超过 7 天/)).toBeVisible()
    await coverage.locator("summary").click()
    const response = page.waitForResponse(
      (r) =>
        r.url().includes(`/${role.endpoint}?`) &&
        new URL(r.url()).searchParams.has("department_id") &&
        r.ok(),
    )
    await coverage
      .getByRole("button", { name: "平台研发部 · 3 人", exact: true })
      .click()
    await response
    await expect(page.locator("tbody tr")).toHaveCount(role.filtered)
    await expect(
      page.locator("tbody tr").filter({ hasText: "平台研发部" }),
    ).toHaveCount(role.filtered)
    if (role.heading === "我的团队") {
      await expect(
        coverage.getByRole("button", { name: /算法与模型部/ }),
      ).toHaveCount(0)
      await expect(coverage.getByText(/Jira/)).toHaveCount(0)
    }
  })
}
