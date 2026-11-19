import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("模块开关、负荷申请、审批和直接调整遵守角色范围", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-capacity-password-2026"
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
    org_name: "合成负荷企业",
    admin_username: `capacity-${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\nM,合成经理,研发,,m-${crypto.randomUUID()}@example.com\nE,合成员工,研发,M,e-${crypto.randomUUID()}@example.com\nO,范围外员工,客户,,o-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  await post(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    admin.access_token,
  )
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const manager = accounts.find((row) => row.employee_code === "M")!
  const employee = accounts.find((row) => row.employee_code === "E")!
  const outside = accounts.find((row) => row.employee_code === "O")!
  await post(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    admin.access_token,
  )
  const tokens = new Map<string, string>()
  for (const account of [manager, employee]) {
    const reset = await post(
      `${base}/accounts/${account.account!.user_id}/reset-password`,
      {},
      admin.access_token,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    tokens.set(account.employee_code!, (await changed.json()).access_token)
  }
  const open = async (token: string, path = "/capacity") => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, orgId }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", orgId)
      },
      { token, orgId: admin.org_id },
    )
    await page.goto(path)
  }
  await open(admin.access_token, "/admin/rules?tab=capacity")
  await expect(
    page.getByRole("checkbox", { name: "开启需求与容量" }),
  ).not.toBeChecked()
  await page.getByRole("checkbox", { name: "开启需求与容量" }).check()
  await page.getByRole("combobox", { name: "谁可以提需求" }).click()
  await page.getByRole("option", { name: "只有部门经理", exact: true }).click()
  await page.getByRole("button", { name: "保存设置", exact: true }).click()
  await expect(page.getByRole("status")).toContainText("需求与容量设置已保存")
  await page.goto("/capacity?tab=team")
  const chart = page.getByRole("table", { name: "团队负荷，按部门、姓名排序" })
  await expect(chart).toBeVisible()
  await expect(
    chart.getByRole("button", { name: "设置", exact: true }),
  ).toHaveCount(0)
  await chart
    .getByRole("row")
    .filter({ hasText: "合成员工" })
    .getByRole("button", { name: "不可用日期" })
    .click()
  const readonlyDates = page.getByRole("dialog", {
    name: "不可用日期 · 合成员工",
  })
  await expect(readonlyDates).toContainText("暂无不可用日期。")
  await expect(
    readonlyDates.getByRole("button", { name: "登记", exact: true }),
  ).toHaveCount(0)
  await readonlyDates.press("Escape")
  await open(tokens.get("E")!)
  await expect(
    page.getByRole("link", { name: "规划", exact: true }),
  ).toBeVisible()
  await expect(page.getByRole("tab", { name: "团队负荷" })).toHaveCount(0)
  await page.goto("/capacity?tab=team")
  await expect(page.getByRole("tab", { name: "我的负荷" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
  await expect(
    page.getByText("无，未接 Jira 或未关联 Jira 账号", { exact: true }),
  ).toBeVisible()
  await expect(
    page
      .getByRole("region", { name: "每周投入" })
      .getByText("未确认", { exact: true }),
  ).toHaveCount(8)
  await expect(page.getByText("有余量", { exact: true })).toHaveCount(0)
  for (const [percent, reason] of [
    ["85", "合成项目占用"],
    ["60", "合成申请待核对"],
  ]) {
    const start = await page.getByLabel("开始周", { exact: true }).inputValue()
    const end = new Date(`${start}T00:00:00Z`)
    end.setUTCDate(end.getUTCDate() + 7)
    await page
      .getByLabel("结束周", { exact: true })
      .fill(end.toISOString().slice(0, 10))
    await page.getByLabel("申请负荷（%）").fill(percent)
    await page.getByLabel("申请原因").fill(reason)
    await page.getByRole("button", { name: "提交申请", exact: true }).click()
    await expect(page.getByRole("status")).toContainText("已有申请待处理")
    await expect(
      page
        .getByRole("region", { name: "每周投入" })
        .getByText("修正待审", { exact: true }),
    ).toHaveCount(2)
    await open(tokens.get("M")!, "/capacity?tab=approvals")
    await expect(
      page.getByRole("link", { name: "规划", exact: true }),
    ).toBeVisible()
    await page.reload()
    await expect(page.getByRole("tab", { name: "待我审批" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    const application = page.getByRole("article", {
      name: "员工的调整申请",
    })
    await expect(application).toContainText(
      `${start} 至 ${end.toISOString().slice(0, 10)}`,
    )
    if (percent === "85")
      await application
        .getByRole("button", { name: "同意", exact: true })
        .click()
    else {
      await expect(
        application.getByRole("button", { name: "退回", exact: true }),
      ).toBeDisabled()
      await application
        .getByLabel("处理说明（退回必填）")
        .fill("请补充项目范围")
      await application
        .getByRole("button", { name: "退回", exact: true })
        .click()
    }
    await expect(application).toHaveCount(0)
    await open(tokens.get("E")!)
    await expect(page.getByText("85%", { exact: true })).toBeVisible()
    await expect(page.getByText("基本满", { exact: true })).toBeVisible()
  }
  await expect(
    page.getByText("处理说明：请补充项目范围", { exact: true }),
  ).toBeVisible()
  await page.goto("/inbox")
  await page
    .getByRole("link", { name: "查看需求与容量", exact: true })
    .first()
    .click()
  await expect(page).toHaveURL("/capacity")
  await open(tokens.get("M")!, "/capacity?tab=team")
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "合成经理" })
      .getByRole("button", { name: "设置", exact: true }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("row").filter({ hasText: "范围外员工" }),
  ).toHaveCount(0)
  await page
    .getByRole("row")
    .filter({ hasText: "合成员工" })
    .getByRole("button", { name: "设置", exact: true })
    .click()
  await expect(page.getByRole("dialog")).toContainText("改后会通知本人")
  await expect(page.getByLabel("调整说明（选填）")).toHaveCount(0)
  const firstWeek = await page
    .getByLabel("开始周", { exact: true })
    .inputValue()
  const nextWeek = new Date(`${firstWeek}T00:00:00Z`)
  nextWeek.setUTCDate(nextWeek.getUTCDate() + 7)
  await page
    .getByLabel("结束周", { exact: true })
    .fill(nextWeek.toISOString().slice(0, 10))
  await page.getByLabel("其他工作占比（%）").fill("110")
  await page.getByRole("button", { name: "确认设置", exact: true }).click()
  const row = chart.getByRole("row").filter({ hasText: "合成员工" })
  await expect(row).toContainText("110%")
  await expect(row.getByText("110% / 100%", { exact: true })).toHaveCount(2)
  await row
    .getByRole("button", { name: /110% \/ 100%，过载，查看负荷明细/ })
    .first()
    .click()
  const details = page.getByRole("dialog", { name: "负荷明细 · 合成员工" })
  await expect(details).toContainText("其他工作占比 110%")
  await expect(
    details.getByRole("button", { name: "设置周投入" }),
  ).toBeVisible()
  await details.press("Escape")
  await page.getByRole("combobox", { name: "部门筛选" }).click()
  await expect(
    page.getByRole("option", { name: "客户", exact: true }),
  ).toHaveCount(0)
  await page.getByRole("option", { name: "研发", exact: true }).click()
  await page.screenshot({
    path: test.info().outputPath("team.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "下一期" }).click()
  await expect(row.getByText("未确认", { exact: true })).toHaveCount(8)
  await page
    .getByRole("button", { name: "沿用上次确认值确认未来 4 周" })
    .click()
  await page.getByRole("button", { name: "回到本周" }).click()
  await expect(row.getByText("110% / 100%", { exact: true })).toHaveCount(4)
  await expect(row.getByText("未确认", { exact: true })).toHaveCount(4)
  const managerHeaders = { Authorization: `Bearer ${tokens.get("M")}` }
  const week = (
    await (
      await request.get(`${base}/capacity/workloads/me`, {
        headers: managerHeaders,
      })
    ).json()
  ).weeks[0].week_start
  const weekly = (employeeId: string, percent: number) => ({
    items: [{ employee_id: employeeId, other_percent: percent }],
    start_week: week,
    end_week: week,
  })
  expect(
    (
      await request.put(`${base}/capacity/weeks`, {
        headers: managerHeaders,
        data: weekly(outside.id, 50),
      })
    ).status(),
  ).toBe(404)
  expect(
    (
      await request.put(`${base}/capacity/weeks`, {
        headers,
        data: weekly(employee.id, 50),
      })
    ).status(),
  ).toBe(403)
  expect(
    (
      await request.get(`${base}/capacity/workloads`, {
        headers: { Authorization: `Bearer ${tokens.get("E")}` },
      })
    ).status(),
  ).toBe(403)
  const adjusted = await request.put(`${base}/capacity/weeks`, {
    headers: managerHeaders,
    data: weekly(employee.id, 50),
  })
  expect(adjusted.ok()).toBeTruthy()
  await open(tokens.get("E")!)
  await expect(page.getByText("有余量", { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: test.info().outputPath("mine-mobile.png"),
    fullPage: true,
  })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 900 })
  const unavailable = page.getByRole("region", { name: "我的不可用日期" })
  await unavailable.getByLabel("开始日期", { exact: true }).fill(week)
  const secondDay = new Date(`${week}T00:00:00Z`)
  secondDay.setUTCDate(secondDay.getUTCDate() + 1)
  await unavailable
    .getByLabel("结束日期", { exact: true })
    .fill(secondDay.toISOString().slice(0, 10))
  await unavailable.getByRole("button", { name: "登记", exact: true }).click()
  await expect(
    unavailable.getByRole("button", { name: "删除", exact: true }),
  ).toHaveCount(1)
  await expect(page.getByRole("region", { name: "每周投入" })).toContainText(
    "可用 60%",
  )
  await unavailable.getByRole("button", { name: "删除", exact: true }).click()
  await expect(
    unavailable.getByRole("button", { name: "删除", exact: true }),
  ).toHaveCount(0)
  await open(tokens.get("M")!, "/capacity?tab=team")
  await page
    .getByRole("row")
    .filter({ hasText: "合成员工" })
    .getByRole("button", { name: "不可用日期", exact: true })
    .click()
  const dates = page.getByRole("dialog")
  const friday = new Date(`${week}T00:00:00Z`)
  friday.setUTCDate(friday.getUTCDate() + 4)
  await dates
    .getByLabel("结束日期", { exact: true })
    .fill(friday.toISOString().slice(0, 10))
  await dates.getByRole("button", { name: "登记", exact: true }).click()
  await expect(dates.getByRole("status")).toContainText(
    "这几周已有分配超出可用，请和部门经理沟通",
  )
  await dates.getByRole("button", { name: "删除", exact: true }).click()
  await expect(
    dates.getByRole("button", { name: "删除", exact: true }),
  ).toHaveCount(0)
  await open(admin.access_token, "/admin/rules?tab=capacity")
  await page.getByRole("checkbox", { name: "开启需求与容量" }).uncheck()
  await page.getByRole("button", { name: "保存设置", exact: true }).click()
  await expect(page.getByRole("status")).toContainText("需求与容量设置已保存")
  for (const token of [
    admin.access_token,
    tokens.get("M")!,
    tokens.get("E")!,
  ]) {
    await open(token)
    await expect(
      page.getByText("需求与容量功能已关闭，如需使用请联系企业管理员", {
        exact: true,
      }),
    ).toBeVisible()
    await expect(
      page.getByRole("link", { name: "规划", exact: true }),
    ).toHaveCount(0)
  }
  await open(tokens.get("M")!, "/find-people")
  await expect(
    page.getByRole("heading", { name: "按能力找人", exact: true }),
  ).toBeVisible()
  await page.goto(`/employees/${employee.id}`)
  await expect(
    page.getByRole("heading", { name: "合成员工", exact: true }),
  ).toBeVisible()
})
