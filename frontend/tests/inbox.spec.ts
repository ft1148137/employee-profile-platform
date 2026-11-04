import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
const username = "employee.platform@demo.example.com"
const password = process.env.DEMO_USER_PASSWORD!

test("employee answers a real question on mobile and updates unread count", async ({
  page,
  request,
}) => {
  const login = await request.post("/api/v1/login/access-token", {
    form: { username, password },
  })
  const headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  }
  const memberships = await request.get("/api/v1/me/memberships", { headers })
  const [membership] = await memberships.json()
  const base = `/api/v1/orgs/${membership.org.id}/me`
  const questions = await request.get(`${base}/questions`, { headers })
  const question = (await questions.json()).data[0]
  expect(question).toBeTruthy()
  const inbox = await request.get(`${base}/inbox`, { headers })
  const before = await inbox.json()
  await logInUser(page, username, password)
  await expect(page).toHaveURL("/inbox")
  await expect(
    page.getByRole("link", {
      name: `收件箱 ${before.unread_count}`,
      exact: true,
    }),
  ).toBeVisible()
  await expect(page.getByText("初始画像待过目", { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  const row = page.getByRole("listitem").filter({ hasText: question.question })
  await row.getByRole("button", { name: "回答问题", exact: true }).click()
  await row
    .getByLabel("你的回答")
    .fill("我完成了合成案例中的排查，并用回归测试验证结果。")
  await row.getByRole("button", { name: "确认回答", exact: true }).click()
  await expect(row.getByRole("status")).toContainText(
    "已回答，已保存为受限自述证据",
  )
  await expect(
    page.getByRole("status").filter({ hasText: "条未读" }),
  ).toHaveText(
    `${before.unread_count - 1} 条未读 · ${before.pending_count - 1} 条待办`,
  )
  const answered = await request.get(`${base}/questions/${question.id}`, {
    headers,
  })
  const result = await answered.json()
  expect(result.status).toBe("answered")
  const evidence = await request.get(
    `/api/v1/orgs/${membership.org.id}/evidence/${result.answer_evidence_id}`,
    { headers },
  )
  expect((await evidence.json()).content.text).toContain("回归测试")
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/inbox-mobile.png",
    fullPage: true,
  })
  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(
    page.getByRole("link", {
      name: `收件箱 ${before.unread_count - 1}`,
      exact: true,
    }),
  ).toBeVisible()
})

test("without todos home opens profile; empty inbox and errors are readable", async ({
  page,
}) => {
  await page.route("**/me/inbox?*", (route) =>
    route.fulfill({
      json: { data: [], count: 0, unread_count: 0, pending_count: 0 },
    }),
  )
  await logInUser(page, username, password)
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await page.getByRole("link", { name: "收件箱", exact: true }).click()
  await expect(page.getByText("收件箱暂时没有消息。")).toBeVisible()
  await page.route("**/me/inbox?*", (route) =>
    route.fulfill({ status: 500, json: { detail: "合成故障" } }),
  )
  await page.reload()
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible()
})
