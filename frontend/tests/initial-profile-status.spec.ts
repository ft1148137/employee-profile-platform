import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
test.use({ storageState: { cookies: [], origins: [] } })

for (const [status, reason] of [
  ["no_records", "还没有你的工作记录"],
  ["no_judgment", "起草时定不了等级"],
] as const) {
  test(`empty profile explains ${status} and guides the employee`, async ({
    page,
  }) => {
    await page.route("**/employees/*/profile", async (route) => {
      const response = await route.fetch()
      const profile = await response.json()
      await route.fulfill({
        json: {
          ...profile,
          latest_published: null,
          employee: {
            ...profile.employee,
            latest_published: null,
            progress: {
              ...profile.employee.progress,
              initial_profile_status: status,
            },
          },
          items: profile.items.map((item: object) => ({
            ...item,
            claim: null,
            status: "unknown",
          })),
        },
      })
    })
    await page.route("**/employees/*/change-requests?status=all", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )
    await logInUser(page, "employee.platform@demo.example.com", password)
    await page.getByRole("link", { name: "我的画像", exact: true }).click()
    const explanation = page.getByRole("region", { name: "画像说明" })
    await expect(explanation).toContainText(reason)
    await expect(explanation).toContainText("添加能力")
    await expect(
      page.getByRole("button", { name: "添加能力", exact: true }),
    ).toBeVisible()
    await expect(page.getByText("已生成候选")).toHaveCount(0)
    await explanation.getByRole("link", { name: "去助手", exact: true }).click()
    await expect(page).toHaveURL(/\/assistant/)
  })
}

test("team cards show initial and review statuses on a phone without horizontal scrolling", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await logInUser(page, "manager.platform@demo.example.com", password)
  await page.goto("/employees")
  const cards = page.getByRole("region", { name: "团队员工卡片" })
  await expect(cards.getByRole("article")).toHaveCount(3)
  for (const card of await cards.getByRole("article").all()) {
    await expect(card.getByRole("link")).toBeVisible()
    await expect(card).toContainText("平台研发部")
    await expect(card).toContainText("初始画像")
    await expect(card).toContainText("审核状态")
    await expect(card.locator("dd").nth(1)).not.toBeEmpty()
  }
  await expect(page.getByRole("table")).toBeHidden()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390)
  await cards.getByRole("link").filter({ hasText: "张伟" }).click()
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("employee settings display the returned direct manager reference", async ({
  page,
}) => {
  await page.route("**/api/v1/orgs/*/me/profile", async (route) => {
    const response = await route.fetch()
    const profile = await response.json()
    await route.fulfill({
      json: {
        ...profile,
        employee: {
          ...profile.employee,
          manager: { id: "synthetic-manager", display_name: "合成直属上级" },
        },
      },
    })
  })
  await logInUser(page, "employee.platform@demo.example.com", password)
  await page.goto("/settings")
  await page.getByRole("tab", { name: "档案", exact: true }).click()
  const fields = page.getByRole("region", { name: "员工信息" })
  await expect(fields).toContainText("直属上级")
  await expect(fields).toContainText("合成直属上级")
})
