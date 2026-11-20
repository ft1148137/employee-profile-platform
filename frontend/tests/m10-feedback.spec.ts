import { expect, test } from "@playwright/test"
import { setupPlans } from "./utils/plans"

test.use({ storageState: { cookies: [], origins: [] } })

test("经理写工作反馈，员工过目后由经理发布", async ({ page, request }) => {
  const {
    api,
    base,
    person,
    a: requester,
    m: manager,
    start,
    later,
    demand,
    admin,
  } = await setupPlans(page, request, true)
  const owner = person("B3")
  const reset = await api(
    `${base}/accounts/${owner.account!.user_id}/reset-password`,
    {},
    admin.access_token,
  )
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok()).toBeTruthy()
  const worker = (
    await api(
      "/api/v1/users/me/password",
      {
        current_password: reset.initial_password,
        new_password: "synthetic-feedback-password-2026",
      },
      (
        await login.json()
      ).access_token,
      "PATCH",
    )
  ).access_token
  const demandUrl = `${base}/capacity/demands/${demand.id}`
  const reply = await api(
    `${demandUrl}/reply`,
    {
      revision: demand.revision,
      outcome: "can_do",
      start_date: start,
      end_date: later(13),
      text: "可以承接",
    },
    manager,
  )
  await api(
    `${demandUrl}/respond`,
    { action: "accept", revision: reply.revision },
    requester,
  )
  await api(
    `${demandUrl}/assignments`,
    {
      employee_id: owner.id,
      allocation_percent: 50,
      start_date: start,
      due_date: later(6),
      outside_basis: true,
    },
    manager,
  )

  const open = async (token: string, path: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, id }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", id)
      },
      { token, id: admin.org_id },
    )
    await page.goto(path)
  }
  const text = "合成反馈：员工完成了项目接口联调和交付验收，并整理了复核记录。"
  await open(manager, `/capacity?tab=incoming&demand=${demand.id}`)
  const department = page.getByRole("region", { name: "部门内分配" })
  await department.getByRole("button", { name: "标记完成" }).click()
  await department.getByRole("button", { name: "写反馈" }).click()
  await expect(
    department.getByRole("checkbox", { name: "合成交付" }),
  ).toBeChecked()
  await department.getByLabel("工作反馈（20～500 字）").fill(text)
  await department.getByRole("button", { name: "发送给员工确认" }).click()
  await expect(department.getByText("已写反馈")).toBeVisible()

  await open(worker, `/employees/${person("B3").id}`)
  await page.locator("summary", { hasText: /^其他待处理请求/ }).click()
  const pending = page.getByRole("region", { name: "其他待处理请求" })
  await pending.getByRole("button", { name: /^工作反馈/ }).click()
  const evidence = page.getByRole("dialog")
  await expect(evidence).toContainText(text)
  await evidence.getByRole("button", { name: "关闭", exact: true }).click()
  await pending.getByRole("button", { name: "确认并提交" }).click()
  await page
    .getByRole("combobox", { name: "等级", exact: true })
    .selectOption("routine")
  await page.getByRole("button", { name: "预览并提交" }).click()
  await page.getByRole("button", { name: "确认提交" }).click()
  await expect(pending.getByText(/待经理审批/)).toBeVisible()

  await open(manager, "/reviews")
  const approvals = (await api(`${base}/reviews`, undefined, manager)).data
  expect(approvals).toHaveLength(1)
  await page.locator(`a[href="/reviews/${approvals[0].id}"]`).click()
  await page.getByLabel("合成交付的审批意见").selectOption("approved")
  await page.getByRole("button", { name: "提交审批" }).click()
  await page.getByRole("button", { name: "确认提交" }).click()
  await expect(page.getByRole("heading", { name: "审批已提交" })).toBeVisible()
  const found = await api(
    `${base}/people-search?capability_key=delivery`,
    undefined,
    manager,
  )
  expect(
    found.data.map((row: { employee_id: string }) => row.employee_id),
  ).toContain(person("B3").id)
})
