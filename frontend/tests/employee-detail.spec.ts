import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin manages account, roles and history from the unified employee detail", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await request.get("/api/v1/me/memberships", { headers })
  const orgId = (await memberships.json())[0].org.id
  const base = `/api/v1/orgs/${orgId}`
  const accounts = await request.get(`${base}/accounts?q=E12`, { headers })
  const employee = (await accounts.json()).data.find(
    (row: { employee_code: string }) => row.employee_code === "E12",
  )
  expect(employee).toBeTruthy()
  const issued = await request.post(`${base}/accounts/issue`, {
    headers,
    data: { employee_ids: [employee.id], regenerate_pending: true },
  })
  expect(issued.ok()).toBeTruthy()
  await page.goto(`/admin/people/${employee.id}`)
  await expect(page).toHaveURL(
    new RegExp(`/employees/${employee.id}\\?tab=account$`),
  )
  await expect(
    page.getByRole("heading", { name: employee.display_name, exact: true }),
  ).toBeVisible()
  const detailTabs = page.getByRole("tablist", {
    name: "员工详情",
    exact: true,
  })
  for (const name of ["画像", "档案", "账号与角色", "Jira 账号", "争议"]) {
    await expect(
      detailTabs.getByRole("tab", { name, exact: true }),
    ).toBeVisible()
  }
  await detailTabs.getByRole("tab", { name: "档案", exact: true }).click()
  await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
    "E12",
  )
  await detailTabs.getByRole("tab", { name: "Jira 账号", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "Jira 账号匹配" }),
  ).toBeVisible()
  await detailTabs.getByRole("tab", { name: "账号与角色", exact: true }).click()
  const history = page.getByRole("region", { name: "修改记录" })
  await expect(history).toContainText("生成账号")
  await page.getByRole("button", { name: "重置密码", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: "确认重置密码" }).click()
  await expect(dialog.getByRole("textbox", { name: "新密码" })).not.toHaveValue(
    "",
  )
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await expect(history).toContainText("重置密码")
  for (const action of ["停用", "启用"]) {
    await page.getByRole("button", { name: action, exact: true }).click()
    await dialog
      .getByRole("button", { name: `确认${action}`, exact: true })
      .click()
    await expect(dialog).toHaveCount(0)
    await expect(history).toContainText(`${action}账号`)
    await expect(
      page.getByRole("region", { name: "账号与角色" }),
    ).toContainText(action === "停用" ? "已停用" : "待首次登录")
  }
  await page.getByRole("button", { name: "授予角色", exact: true }).click()
  await dialog.getByRole("combobox", { name: "账号", exact: true }).click()
  await page.getByRole("option").click()
  await dialog.getByRole("combobox", { name: "部门", exact: true }).click()
  await page.getByRole("option", { name: "平台研发部", exact: true }).click()
  await dialog.getByRole("button", { name: "确认授予", exact: true }).click()
  await expect(dialog.getByRole("status")).toContainText("角色已授予")
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await page.getByRole("button", { name: "撤销角色", exact: true }).click()
  await dialog.getByRole("button", { name: "确认撤销角色" }).click()
  await expect(history).toContainText("撤销角色")
  const reloadedHistory = page.waitForResponse(
    (response) =>
      response.url().includes(`${base}/audit-events?`) &&
      new URL(response.url()).searchParams.get("employee_id") === employee.id,
  )
  await page.reload()
  expect((await reloadedHistory).ok()).toBeTruthy()
  await expect(history).toContainText("授予角色")
  await expect(history).toContainText("撤销角色")
  await expect(history).toContainText("网页")
  await expectChineseCopy(page, ["demo-jira-12"])
  await page.screenshot({
    path: "test-results/admin-person.png",
    fullPage: true,
  })
  await page.evaluate(() => localStorage.setItem("vite-ui-theme", "light"))
  await page.reload()
  await expect(page.getByRole("region", { name: "账号与角色" })).toBeVisible()
  await page.screenshot({
    path: "test-results/admin-person-light.png",
    fullPage: true,
  })
})

for (const username of ["manager.platform", "employee.platform"]) {
  test(`${username} sees only compact profile, including a restricted tab URL`, async ({
    page,
    request,
  }) => {
    const password = process.env.DEMO_USER_PASSWORD!
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: `${username}@demo.example.com`, password },
    })
    const headers = {
      Authorization: `Bearer ${(await login.json()).access_token}`,
    }
    const membership = (
      await (await request.get("/api/v1/me/memberships", { headers })).json()
    )[0]
    const visible = (
      await (
        await request.get(`/api/v1/orgs/${membership.org.id}/employees`, {
          headers,
        })
      ).json()
    ).data
    const subject =
      visible.find(
        (employee: { id: string }) => employee.id !== membership.employee_id,
      ) ?? visible[0]
    await logInUser(page, `${username}@demo.example.com`, password)
    const adminRequests: string[] = []
    page.on("request", (request) => {
      if (
        /\/(accounts|role-grants|external-identities|appeals)(\?|$)/.test(
          request.url(),
        )
      )
        adminRequests.push(request.url())
    })
    await page.goto(`/employees/${subject.id}?tab=account`)
    await expect(
      page.getByRole("tablist", { name: "员工详情", exact: true }),
    ).toHaveCount(0)
    await expect(page.getByRole("region", { name: "员工信息" })).toHaveCount(0)
    for (const tab of ["证据", "版本历史", "档案"])
      await expect(
        page.getByRole("tab", { name: tab, exact: true }),
      ).toHaveCount(0)
    await page.goto(`/employees/${subject.id}?tab=fields`)
    await page.reload()
    await expect(page.getByRole("region", { name: "员工信息" })).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "调岗", exact: true }),
    ).toHaveCount(0)
    expect(adminRequests).toEqual([])
    for (const path of [
      "accounts",
      "role-grants",
      "external-identities",
      "appeals",
    ]) {
      expect(
        (
          await request.get(`/api/v1/orgs/${membership.org.id}/${path}`, {
            headers,
          })
        ).status(),
      ).toBe(403)
    }
    const adminLogin = await request.post("/api/v1/login/access-token", {
      form: { username: "admin@demo.example.com", password },
    })
    const all = (
      await (
        await request.get(`/api/v1/orgs/${membership.org.id}/employees`, {
          headers: {
            Authorization: `Bearer ${(await adminLogin.json()).access_token}`,
          },
        })
      ).json()
    ).data
    const outsider = all.find(
      (employee: { id: string }) =>
        !visible.some((row: { id: string }) => row.id === employee.id),
    )
    expect(outsider).toBeTruthy()
    await page.goto(`/employees/${outsider.id}`)
    await expect(page.getByText("未找到该员工或你没有权限查看")).toBeVisible()
  })
}

test("link a searched Jira account to this employee and unlink it", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const orgId = (
    await (await request.get("/api/v1/me/memberships", { headers })).json()
  )[0].org.id
  const base = `/api/v1/orgs/${orgId}`
  const employee = (
    await (await request.get(`${base}/accounts?q=E12`, { headers })).json()
  ).data[0]
  const identities = (
    await (
      await request.get(
        `${base}/external-identities?status=all&source=jira&q=demo-jira-12`,
        { headers },
      )
    ).json()
  ).data
  const identity = identities.find(
    (row: { external_id: string }) => row.external_id === "demo-jira-12",
  )
  expect(identity).toBeTruthy()
  if (identity.employee)
    expect(
      (
        await request.post(
          `${base}/external-identities/${identity.id}/unlink`,
          { headers },
        )
      ).ok(),
    ).toBeTruthy()
  await page.goto(`/employees/${employee.id}?tab=identities`)
  await page.getByRole("tab", { name: "未关联", exact: true }).click()
  await page.getByLabel("搜索账号、名字或邮箱").fill(identity.external_id)
  await page
    .getByRole("combobox", { name: "关联账号", exact: true })
    .selectOption(identity.id)
  await page.getByRole("button", { name: "关联到此员工", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("button", { name: "确认关联到此员工", exact: true })
    .click()
  const row = page.getByRole("row").filter({ hasText: identity.external_id })
  await expect(row).toContainText(employee.display_name)
  await page.reload()
  await expect(row).toBeVisible()
  await row.getByRole("button", { name: "解除关联" }).click()
  await dialog.getByRole("button", { name: "确认解除", exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(row).toHaveCount(0)
  await expect(page.getByRole("status")).toContainText("已撤销")
  const unlinked = (
    await (
      await request.get(
        `${base}/external-identities?status=unlinked&q=demo-jira-12`,
        { headers },
      )
    ).json()
  ).data
  expect(
    unlinked.some((entry: { id: string }) => entry.id === identity.id),
  ).toBeTruthy()
})
