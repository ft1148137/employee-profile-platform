import { expect, test } from "@playwright/test"
import type { EmployeeAccount, WorkloadList } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("团队负荷在手机横向滑动后仍显示姓名和设置", async ({ page, request }) => {
  const password = "synthetic-mobile-workload-password-2026"
  const post = async (url: string, data: object, token?: string) => {
    const response = await request.post(url, {
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const setup = await post("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await post("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成手机负荷企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const root = admin.access_token
  const settings = await request.put(`${base}/capacity-settings`, {
    headers: { Authorization: `Bearer ${root}` },
    data: { enabled: true },
  })
  expect(settings.ok(), await settings.text()).toBeTruthy()
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${root}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nE,A合成员工,研发,M,e-${crypto.randomUUID()}@example.com\nM,Z合成经理,研发,,m-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await post(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    root,
  )
  const issued = await request.post(`${base}/accounts/issue`, {
    headers: { Authorization: `Bearer ${root}` },
    data: {},
  })
  expect(issued.ok(), await issued.text()).toBeTruthy()
  const accountsResponse = await request.get(`${base}/accounts`, {
    headers: { Authorization: `Bearer ${root}` },
  })
  expect(accountsResponse.ok()).toBeTruthy()
  const accounts: EmployeeAccount[] = (await accountsResponse.json()).data
  const manager = accounts.find((row) => row.employee_code === "M")!
  await post(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    root,
  )
  const reset = await post(
    `${base}/accounts/${manager.account!.user_id}/reset-password`,
    {},
    root,
  )
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok()).toBeTruthy()
  const changed = await request.patch("/api/v1/users/me/password", {
    headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
    data: { current_password: reset.initial_password, new_password: password },
  })
  expect(changed.ok(), await changed.text()).toBeTruthy()
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token: (await changed.json()).access_token, orgId: admin.org_id },
  )

  await page.route(`**${base}/capacity/workloads?**`, async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBeTruthy()
    const workload: WorkloadList = await response.json()
    const row = workload.data.find(
      (item) => item.employee_name === "A合成员工",
    )!
    const date = (days: number) => {
      const day = new Date(row.weeks[0].week_start)
      day.setUTCDate(day.getUTCDate() + days)
      return day.toISOString().slice(0, 10)
    }
    const common = {
      demand_id: "synthetic-demand",
      capability_name: "研发",
      requesting_department_name: "合成部门",
      allocation_percent: 25,
    }
    row.assignments = [
      {
        ...common,
        id: "committed",
        kind: "committed",
        title: "合成已承诺需求",
        start_date: date(-7),
        due_date: date(9),
      },
      {
        ...common,
        id: "borrow",
        kind: "borrow",
        title: "合成借调需求",
        start_date: date(3),
        due_date: date(60),
      },
    ]
    row.holds = [
      {
        ...common,
        proposal_id: "synthetic-proposal",
        is_borrow: false,
        title: "合成方案",
        start_date: date(4),
        end_date: date(12),
        expires_at: `${date(6)}T12:00:00Z`,
      },
    ]
    const expires_at = row.holds[0].expires_at
    for (const [index, week] of row.weeks.entries()) {
      const hasVisibleHold = index < 2
      week.holds = hasVisibleHold
        ? [{ allocation_percent: 25, expires_at }]
        : []
      if (index === 0) week.holds.push({ allocation_percent: 20, expires_at })
      week.hold_percent = week.holds.reduce(
        (total, hold) => total + hold.allocation_percent,
        0,
      )
      week.platform_percent = (hasVisibleHold ? 50 : 25) + week.hold_percent
    }
    row.unavailabilities = [
      { id: "unavailable", start_date: date(3), end_date: date(4) },
    ]
    await route.fulfill({ response, json: workload })
  })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/capacity?tab=team")
  const card = page.getByRole("article", { name: "A合成员工" })
  const hint = card.getByText("左右滑动查看 8 周排期", { exact: true })
  const name = card.getByText("A合成员工", { exact: true })
  const setting = card.getByRole("button", { name: "设置", exact: true })
  const dates = card.getByRole("button", { name: "不可用日期", exact: true })
  await expect(hint).toBeVisible()
  await expect(name).toBeInViewport()
  await expect(setting).toBeInViewport()
  await expect(dates).toBeInViewport()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390)

  const scroller = card.getByRole("region", { name: "A合成员工的 8 周排期" })
  const committed = scroller.getByRole("button", {
    name: /^已分配任务：合成已承诺需求/,
  })
  const borrow = scroller.getByRole("button", {
    name: /^跨部门借调：借调 · 合成借调需求/,
  })
  await expect(committed).toContainText("←")
  await expect(borrow).toContainText("→")
  await expect(
    scroller.getByRole("button", { name: /^不可用：不可用日期/ }),
  ).toBeVisible()
  await scroller
    .getByRole("button", { name: /^方案暂留：暂留 · 合成方案/ })
    .click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("负荷明细 · A合成员工")
  await expect(dialog).toContainText("其他工作占比")
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  )
  await expect(page.locator('[data-slot="sheet-overlay"]')).toHaveCount(0)
  await expect(
    dialog.getByText("另有未展开或待确认负荷 20%", { exact: true }),
  ).toBeVisible()
  await expect(dialog.getByText("另有未展开或待确认负荷")).toHaveCount(1)
  await expect(
    dialog.getByRole("region", { name: "已分配任务" }),
  ).toContainText("合成已承诺需求 · 25%")
  await expect(
    dialog.getByRole("region", { name: "跨部门借调" }),
  ).toContainText("合成借调需求 · 25%")
  await expect(dialog.getByRole("region", { name: "方案暂留" })).toContainText(
    "待确认，到期",
  )
  await expect(
    dialog.getByRole("button", { name: "登记不可用日期" }),
  ).toBeVisible()
  await dialog.press("Escape")
  await expect(dialog).toBeHidden()
  expect(
    await scroller.evaluate((element) => {
      element.scrollLeft = element.scrollWidth
      return element.scrollLeft
    }),
  ).toBeGreaterThan(0)
  await expect(name).toBeInViewport()
  await expect(setting).toBeInViewport()
  await expect(dates).toBeInViewport()
  await setting.click()
  await expect(dialog).toContainText("设置周投入 · A合成员工")
  await dialog.press("Escape")
  await expect(dialog).toBeHidden()

  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(hint).toBeHidden()
  const table = page.getByRole("table", { name: "团队负荷，按部门、姓名排序" })
  await expect(table.getByText("本周", { exact: true })).toBeVisible()
  await expect(
    table.getByRole("button", { name: /^已分配任务：合成已承诺需求/ }),
  ).toBeVisible()
})
