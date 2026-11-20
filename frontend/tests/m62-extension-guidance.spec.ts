import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { importRoster } from "./utils/roster"

test.use({ storageState: { cookies: [], origins: [] } })

test("延期显示审批人、受阻指引和代理标示，代理给目标经理后可确认承诺", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-guidance-password-2026"
  const api = async (
    url: string,
    token?: string,
    data?: object,
    method = data ? "POST" : "GET",
  ) => {
    const response = await request.fetch(url, {
      method,
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.headers()["content-type"]?.includes("application/json")
      ? response.json()
      : null
  }
  const setup = await api("/api/v1/private/setup-tokens", undefined, {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", undefined, {
    token: setup.token,
    org_name: "合成延期指引企业",
    admin_username: `${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const root = admin.access_token
  const open = async (token: string, path: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(path)
  }
  await api(`${base}/capacity-settings`, root, { enabled: true }, "PUT")
  await open(root, "/capacity")
  await importRoster(
    page,
    Buffer.from(
      `工号,姓名,部门,上级工号,邮箱\nA,合成客户,客户,,a@example.com\nM,合成目标经理,研发,,m@example.com\nC,合成原审批人,研发/下级,,c@example.com\nE,合成员工,研发/下级,C,e@example.com\nD,合成代理,研发/下级,,d@example.com\n`.replace(
        /@example\.com/g,
        `-${crypto.randomUUID()}@example.com`,
      ),
    ),
  )
  await api(`${base}/accounts/issue`, root, {})
  const accounts: EmployeeAccount[] = (await api(`${base}/accounts`, root)).data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  for (const code of ["M", "C"])
    await api(`${base}/role-grants`, root, {
      user_id: person(code).account!.user_id,
      role: "dept_manager",
      department_id: person(code).department!.id,
    })
  const tokens = new Map<string, string>()
  for (const code of ["A", "M", "C", "E", "D"]) {
    const reset = await api(
      `${base}/accounts/${person(code).account!.user_id}/reset-password`,
      root,
      {},
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await api(
      "/api/v1/users/me/password",
      (await login.json()).access_token,
      {
        current_password: reset.initial_password,
        new_password: password,
      },
      "PATCH",
    )
    tokens.set(code, changed.access_token)
  }
  await api(`${base}/capabilities/versions`, root, {
    expected_version: 0,
    definitions: [
      {
        key: "delivery",
        name: "合成交付",
        category: "工程",
        description: "合成能力",
        level_rubric: {
          levels: [
            { key: "routine", label: "独立完成", description: "独立完成" },
          ],
        },
      },
    ],
  })
  const demand = await api(`${base}/capacity/demands`, tokens.get("A"), {
    requesting_department_id: person("A").department!.id,
    target_department_id: person("M").department!.id,
    capability_key: "delivery",
    headcount: 1,
    allocation_percent: 30,
    window_start: "2026-12-01",
    window_end: "2026-12-31",
    description: "合成需求",
  })
  const reply = await api(
    `${base}/capacity/demands/${demand.id}/reply`,
    tokens.get("M"),
    {
      revision: demand.revision,
      outcome: "can_do",
      start_date: "2026-12-01",
      end_date: "2026-12-31",
      text: "可以承接",
    },
  )
  await api(`${base}/capacity/demands/${demand.id}/respond`, tokens.get("A"), {
    action: "accept",
    revision: reply.revision,
  })
  await api(
    `${base}/capacity/weeks`,
    tokens.get("M"),
    {
      items: [{ employee_id: person("E").id, other_percent: 0 }],
      start_week: "2026-11-30",
      end_week: "2027-01-04",
    },
    "PUT",
  )
  await api(
    `${base}/capacity/demands/${demand.id}/assignments`,
    tokens.get("M"),
    {
      employee_id: person("E").id,
      allocation_percent: 30,
      start_date: "2026-12-01",
      outside_basis: true,
      due_date: "2026-12-20",
    },
  )
  await open(tokens.get("E")!, "/capacity?tab=assignments")
  const form = page.getByRole("form", { name: "申请延期", exact: true })
  await expect(form).toContainText("将交给 合成原审批人 审批")
  await form.getByLabel("申请完成日期").fill("2027-01-05")
  await form.getByLabel("延期原因").fill("合成延期原因")
  await form.getByRole("button", { name: "提交延期申请" }).click()
  await expect(page.getByRole("status")).toContainText(
    "已提交，等待 合成原审批人 审批",
  )
  await api(`${base}/capacity/workloads/me/requests`, tokens.get("E"), {
    requested_percent: 40,
    start_week: (await api(`${base}/capacity/workloads/me`, tokens.get("E")))
      .weeks[0].week_start,
    end_week: (await api(`${base}/capacity/workloads/me`, tokens.get("E")))
      .weeks[0].week_start,
    reason: "合成负荷调整",
  })
  await open(tokens.get("C")!, "/capacity?tab=approvals")
  const extension = page.getByRole("form", { name: "合成员工的延期申请" })
  await expect(extension).toContainText("需由 合成目标经理 确认")
  await expect(extension).toContainText(
    "在「设置 → 偏好与代理」中授权其代理审批",
  )
  await expect(extension).toContainText("2026-12-31 及之前")
  const delegate = async (code: string) => {
    await page.goto("/settings")
    await page.getByRole("link", { name: "偏好与代理", exact: true }).click()
    await page
      .getByLabel("代理人", { exact: true })
      .selectOption(person(code).account!.user_id)
    const localDate = (delta: number) => {
      const value = new Date(Date.now() + delta)
      return new Date(value.getTime() - value.getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16)
    }
    await page.getByLabel("开始时间").fill(localDate(-60000))
    await page.getByLabel("结束时间").fill(localDate(3600000))
    await page.getByRole("button", { name: "保存代理" }).click()
    await expect(page.getByRole("region", { name: "代理记录" })).toContainText(
      "代理中",
    )
  }
  await delegate("D")
  await open(tokens.get("D")!, "/capacity?tab=approvals")
  await expect(extension).toContainText(
    "代 合成原审批人 审批（原审批人：合成原审批人）",
  )
  await expect(extension).toContainText(
    "请原审批人 合成原审批人 把审批代理给 合成目标经理",
  )
  await expect(
    page.getByRole("article", { name: "员工的调整申请" }),
  ).toContainText("代 合成原审批人 审批（原审批人：合成原审批人）")
  await open(tokens.get("C")!, "/settings")
  await page.getByRole("link", { name: "偏好与代理", exact: true }).click()
  await page.getByRole("button", { name: "撤销代理" }).click()
  await expect(page.getByRole("region", { name: "代理记录" })).toContainText(
    "已撤销",
  )
  await delegate("M")
  await open(tokens.get("M")!, "/capacity?tab=approvals")
  await expect(extension).toContainText("代 合成原审批人 审批")
  await extension.getByLabel("新的承诺完成日期").fill("2027-01-05")
  await extension.getByLabel("给对方部门的说明").fill("调整部门交付安排")
  await extension.getByRole("button", { name: "同意延期" }).click()
  await expect(extension).toHaveCount(0)
  expect(
    (await api(`${base}/capacity/demands/${demand.id}`, tokens.get("A")))
      .committed_end_date,
  ).toBe("2027-01-05")
})
