import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
const username = "employee.algorithm@demo.example.com"
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
  await logInUser(page, "employee.platform@demo.example.com", password)
  await expect(page).toHaveURL("/assistant")
  await page.goto("/inbox")
  await expect(
    page
      .getByRole("complementary", { name: "消息列表" })
      .getByText("初始画像待确认", { exact: true }),
  ).toBeVisible()
  await logOutUser(page)
  await logInUser(page, username, password)
  await expect(page).toHaveURL("/assistant")
  await page.goto("/inbox")
  await expect(
    page.getByRole("link", {
      name: `收件箱 ${before.unread_count}`,
      exact: true,
    }),
  ).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  const row = page.getByRole("listitem").filter({ hasText: question.question })
  await row.getByRole("button").click()
  const detail = page.getByRole("region", { name: "消息详情" })
  const replies = detail
    .getByRole("group", { name: "快捷答复" })
    .getByRole("button")
  const answer = detail.getByLabel("你的回答")
  await expect(replies).toHaveCount(3)
  await replies.first().click()
  await expect(replies.first()).toHaveAttribute("aria-pressed", "true")
  await expect(answer).toBeFocused()
  await expect(answer).toHaveValue("情况属实，我独立负责了相关工作")
  await replies.nth(1).click()
  await expect(replies.first()).toHaveAttribute("aria-pressed", "false")
  await expect(answer).toHaveValue("仅参与协作支持，非主要负责人")
  const stillOpen = await request.get(`${base}/questions/${question.id}`, {
    headers,
  })
  expect((await stillOpen.json()).status).toBe("open")
  await answer.fill(
    "仅参与协作支持，非主要负责人。我完成了合成案例中的排查，并用回归测试验证结果。",
  )
  await expect(replies.nth(1)).toHaveAttribute("aria-pressed", "false")
  await detail.getByRole("button", { name: "提交回答", exact: true }).click()
  await expect(detail.getByRole("status")).toContainText(
    "已回答，已保存为受限自述证据",
  )
  await expect(answer).toHaveCount(0)
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
  const record = await evidence.json()
  expect(record.visibility).toBe("restricted")
  expect(record.content.text).toContain("回归测试")
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
      name:
        before.unread_count > 1
          ? `收件箱 ${before.unread_count - 1}`
          : "收件箱",
      exact: true,
    }),
  ).toBeVisible()
})

test("without todos home opens assistant; empty inbox and errors are readable", async ({
  page,
}) => {
  await page.route("**/me/inbox?*", (route) =>
    route.fulfill({
      json: { data: [], count: 0, unread_count: 0, pending_count: 0 },
    }),
  )
  await logInUser(page, username, password)
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "收件箱", exact: true }).click()
  await expect(page.getByText("暂无消息")).toBeVisible()
  await page.route("**/me/inbox?*", (route) =>
    route.fulfill({ status: 500, json: { detail: "合成故障" } }),
  )
  await page.reload()
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible()
})

test("inbox filters, marks the page as read and keeps the answered question open", async ({
  page,
}) => {
  const rows = [
    {
      id: "task",
      question_id: "task",
      kind: "question",
      message: "合成待确认通知",
    },
    { id: "notice", kind: "profile_edited", message: "合成画像通知" },
    { id: "read", kind: "profile_edited", message: "已读画像通知" },
  ]
  const read = new Set(["read"])
  let answered = false
  const timestamp = new Date().toISOString()
  await page.route("**/me/inbox?*", (route) =>
    route.fulfill({
      json: {
        count: rows.length,
        unread_count: rows.length - read.size,
        pending_count: answered ? 0 : 1,
        data: rows.map((row) => ({
          ...row,
          created_at: timestamp,
          read_at: read.has(row.id) ? timestamp : null,
          done_at: row.id === "task" && answered ? timestamp : null,
        })),
      },
    }),
  )
  await page.route("**/me/inbox/*/read", (route) => {
    const segments = new URL(route.request().url()).pathname.split("/")
    read.add(segments[segments.length - 2])
    return route.fulfill({ json: {} })
  })
  await page.route("**/me/questions/task", (route) =>
    route.fulfill({
      json: { reason: "核对合成任务", status: answered ? "answered" : "open" },
    }),
  )
  await page.route("**/me/questions/task/answer", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      answer: "我补充了合成任务的分工。",
    })
    answered = true
    return route.fulfill({ json: { status: "answered" } })
  })
  await logInUser(page, username, password)
  await page.goto("/inbox")
  const list = page.getByRole("complementary", { name: "消息列表" })
  await list.getByRole("button", { name: /^待办/ }).click()
  await expect(list.getByRole("listitem")).toHaveCount(1)
  await list.getByRole("button", { name: /^全部/ }).click()
  await list.getByRole("button", { name: "筛选" }).click()
  await page.getByRole("menuitemradio", { name: "画像已更新" }).click()
  await expect(list.getByRole("listitem")).toHaveCount(2)
  await list.getByRole("button", { name: "筛选" }).click()
  await page.getByRole("menuitemcheckbox", { name: "仅看未读" }).click()
  await expect(list.getByRole("listitem")).toHaveCount(1)
  await page.getByRole("button", { name: "全部标为已读" }).click()
  await expect(page.getByRole("status")).toHaveText("0 条未读 · 1 条待办")
  expect([...read].sort()).toEqual(["notice", "read", "task"])
  await expect(list.getByRole("listitem")).toHaveCount(0)
  await page.reload()
  await list.getByRole("button", { name: /^待办/ }).click()
  const detail = page.getByRole("region", { name: "消息详情" })
  await detail.getByLabel("你的回答").fill("我补充了合成任务的分工。")
  await detail.getByRole("button", { name: "提交回答" }).click()
  await expect(list.getByRole("listitem")).toHaveCount(0)
  await expect(detail.getByRole("status")).toHaveText(
    "已回答，已保存为受限自述证据",
  )
})
