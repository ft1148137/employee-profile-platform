import { expect, type Page, test } from "@playwright/test"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
const selfReport = "我独立完成了分页接口，冲突处理还需要同事复核。"
test.use({
  viewport: { width: 1440, height: 1080 },
  storageState: { cookies: [], origins: [] },
})

async function openE02(page: Page, email: string) {
  await logInUser(page, email, password)
  if (email !== "employee.platform@demo.example.com") {
    await page.goto("/employees")
    await page.getByRole("combobox", { name: "部门筛选" }).click()
    await page.getByRole("option", { name: "平台研发部", exact: true }).click()
    await page.getByRole("row").filter({ hasText: "张伟" }).click()
  }
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
}

test("employee reads capability, Jira evidence and earlier published snapshot", async ({
  page,
}) => {
  await openE02(page, "employee.platform@demo.example.com")
  const api = page.getByRole("article", { name: "后端 API 开发", exact: true })
  await expect(api.getByText("已确认", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("article").filter({ hasText: "未知：暂无足够证据" }),
  ).toHaveCount(11)
  await expect(
    api.getByText("能独立完成复杂任务", { exact: true }),
  ).toBeVisible()
  await api.getByRole("button").hover()
  await expect(page.getByRole("tooltip")).toContainText("不是经过校准的概率")
  await page.keyboard.press("Escape")
  await expect(page.getByRole("tooltip")).toHaveCount(0)
  await page.screenshot({
    animations: "disabled",
    path: "test-results/profile-capabilities.png",
    fullPage: true,
  })
  await api.locator("summary").click()
  await api.getByRole("button", { name: /实现分页查询接口/ }).click()
  await expect(page.getByRole("dialog")).toContainText("PLAT-104")
  await expect(page.getByRole("dialog")).toContainText("背景")
  await page.getByRole("button", { name: "关闭", exact: true }).click()
  await page.getByRole("tab", { name: "版本历史" }).click()
  await expect(page.getByRole("tabpanel").getByRole("listitem")).toHaveCount(2)
  await page.getByRole("button", { name: /^r1 ·/ }).click()
  const snapshot = page.getByRole("region", { name: "r1 只读快照" })
  await expect(
    snapshot
      .getByRole("article", { name: "后端 API 开发", exact: true })
      .getByText("暂定", { exact: true }),
  ).toBeVisible()
  await page.screenshot({
    animations: "disabled",
    path: "test-results/profile-versions.png",
    fullPage: true,
  })
  await page.getByRole("tab", { name: "能力画像" }).click()
  await expect(api.getByText("已确认", { exact: true })).toBeVisible()
})

for (const [role, email, restricted] of [
  ["admin", "admin@demo.example.com", true],
  ["manager", "manager.platform@demo.example.com", false],
  ["employee", "employee.platform@demo.example.com", false],
] as const) {
  test(`${role} sees the permitted self-report content`, async ({ page }) => {
    await openE02(page, email)
    await page.getByRole("tab", { name: "证据", exact: true }).click()
    await page.getByLabel("证据类型").selectOption("self_report")
    await expect(page.getByRole("tabpanel").getByRole("listitem")).toHaveCount(
      1,
    )
    await page.getByRole("tabpanel").getByRole("button").click()
    const sheet = page.getByRole("dialog")
    await expect(sheet).toContainText("SR02")
    await expect(sheet.getByRole("listitem")).toHaveCount(1)
    await expect(sheet.getByRole("listitem")).toHaveText("后端 API 开发 · 支持")
    if (restricted) {
      await expect(sheet).toContainText(
        "受限内容：仅本人和管辖该部门的经理可见",
      )
      await expect(page.getByText(selfReport)).toHaveCount(0)
    } else await expect(sheet).toContainText(selfReport)
    await page.screenshot({
      animations: "disabled",
      path: `test-results/profile-evidence-${role}.png`,
      fullPage: true,
    })
  })
}

test("profile recovers from errors and tolerates incomplete evidence fields", async ({
  page,
}) => {
  await page.route("**/employees/*/profile", (route) =>
    route.fulfill({ status: 500, json: { detail: "Temporary failure" } }),
  )
  await logInUser(page, "employee.platform@demo.example.com", password)
  await expect(page.getByRole("alert")).toBeVisible()
  await page.unroute("**/employees/*/profile")
  await page.getByRole("button", { name: "重试" }).click()
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
  await page.route("**/evidence/*", async (route) => {
    const response = await route.fetch()
    const evidence = await response.json()
    await route.fulfill({
      json: {
        ...evidence,
        content: { text: 42, related_task_keys: null },
        kind: "self_report",
      },
    })
  })
  await page.getByRole("tab", { name: "证据", exact: true }).click()
  await page.getByRole("tabpanel").getByRole("button").first().click()
  await expect(page.getByRole("dialog")).toContainText("关联任务：—")
  await expect(page.getByRole("dialog")).toContainText("来源与导入信息")
})
