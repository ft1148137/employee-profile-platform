import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({
  viewport: { width: 1600, height: 1000 },
  storageState: { cookies: [], origins: [] },
})

for (const role of [
  {
    email: "admin@demo.example.com",
    label: "员工",
    total: 12,
    filtered: 3,
    endpoint: "accounts",
  },
  {
    email: "manager.platform@demo.example.com",
    label: "我的团队",
    total: 3,
    filtered: 3,
    endpoint: "employees",
  },
]) {
  test(`${role.label} shows scoped coverage and filters the list by department`, async ({
    page,
  }) => {
    await logInUser(page, role.email, process.env.DEMO_USER_PASSWORD!)
    await page.goto("/employees")
    await expect(
      page.getByRole("heading", { name: "员工", exact: true }),
    ).toBeVisible()
    const coverage = page.getByRole("region", { name: "画像覆盖率" })
    await expect(
      coverage.getByText(`范围内在职员工 ${role.total} 人`),
    ).toBeVisible()
    for (const status of [
      "已发布",
      "存在争议",
      "待本人确认",
      "待审批",
      "未知",
    ]) {
      await expect(
        coverage.getByText(status, { exact: true }).first(),
      ).toBeVisible()
    }
    const pending = coverage.getByRole("button", { name: /^待本人确认/ })
    const pendingCount = Number(await pending.locator("strong").textContent())
    expect(pendingCount).toBeGreaterThan(0)
    const rows = page.locator("tbody tr")
    if (role.endpoint === "accounts") {
      await page.getByRole("combobox", { name: "每页条数" }).click()
      await page.getByRole("option", { name: "5", exact: true }).click()
      await page.getByRole("button", { name: "下一页", exact: true }).click()
      await expect(page.getByText("第 6–10 条，共 12 条")).toBeVisible()
    }
    await pending.click()
    await expect(pending).toHaveAttribute("aria-pressed", "true")
    await expect(
      page.getByText(new RegExp(`共 ${pendingCount} 名员工`)),
    ).toBeVisible()
    await expect(rows).toHaveCount(pendingCount)
    await expect(rows.filter({ hasText: "张伟" })).toHaveCount(1)
    await pending.click()
    await expect(pending).toHaveAttribute("aria-pressed", "false")
    await expect(
      page.getByText(new RegExp(`共 ${role.total} 名员工`)),
    ).toBeVisible()
    await expect(rows).toHaveCount(
      role.endpoint === "accounts" ? 5 : role.total,
    )
    await coverage.getByRole("button", { name: "按部门分布" }).click()
    await expect(coverage.getByText(/等待审批超过 7 天/)).toBeVisible()
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
    await pending.click()
    const filteredSearch = page.waitForResponse((r) => {
      const { pathname, searchParams } = new URL(r.url())
      return (
        pathname.endsWith(`/${role.endpoint}`) &&
        searchParams.has("department_id") &&
        searchParams.get("review_status") === "pending_employee" &&
        searchParams.get("q") === "张伟" &&
        r.ok()
      )
    })
    await page.getByRole("textbox", { name: /^搜索/ }).fill("张伟")
    await filteredSearch
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText("张伟")
    await expect(page.getByText(/共 1 名员工/)).toBeVisible()
    if (role.label === "我的团队") {
      await expect(
        page.getByRole("link", { name: "我的团队", exact: true }),
      ).toBeVisible()
      await expect(
        coverage.getByRole("button", { name: /算法与模型部/ }),
      ).toHaveCount(0)
      await expect(coverage.getByText(/Jira/)).toHaveCount(0)
    }
  })
}
