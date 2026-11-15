import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("关闭模块后刷新菜单或操作提示，直接调整不再收集说明", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
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
    org_name: "合成模块关闭企业",
    admin_username: `disabled-${crypto.randomUUID()}@example.com`,
    admin_password: "synthetic-disabled-password-2026",
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
          `工号,姓名,部门,上级工号,邮箱\nM,合成经理,研发,,m-${crypto.randomUUID()}@example.com\nE,合成员工,研发,M,e-${crypto.randomUUID()}@example.com\n`,
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
  for (const account of accounts) {
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
        new_password: "synthetic-disabled-password-2026",
      },
    })
    expect(changed.ok()).toBeTruthy()
    tokens.set(account.employee_code!, (await changed.json()).access_token)
  }
  const setEnabled = async (enabled: boolean) => {
    const response = await request.put(`${base}/capacity-settings`, {
      headers,
      data: { enabled },
    })
    expect(response.ok()).toBeTruthy()
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
  const menu = page.getByRole("link", { name: "需求与容量", exact: true })
  for (const trigger of ["mutation", "query", "focus"]) {
    await setEnabled(true)
    await open(tokens.get("E")!)
    await expect(menu).toBeVisible()
    await expect(page.getByLabel("申请负荷（%）")).toBeVisible()
    if (trigger === "mutation") {
      await page.getByLabel("申请负荷（%）").fill("40")
      await page.getByLabel("申请原因").fill("合成调整原因")
    }
    await setEnabled(false)
    if (trigger === "focus") {
      await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "hidden",
        })
        window.dispatchEvent(new Event("visibilitychange"))
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "visible",
        })
        window.dispatchEvent(new Event("visibilitychange"))
      })
    } else if (trigger === "query") {
      await page.getByRole("tab", { name: "我的分配" }).click()
    } else {
      await page.getByRole("button", { name: "提交申请", exact: true }).click()
    }
    await expect(
      page.getByRole("status").filter({ hasText: "需求与容量模块未开启" }),
    ).toBeVisible()
    await expect(menu).toHaveCount(0)
    await expect(page.getByRole("alert")).toHaveCount(0)
    await expect(page.locator("[data-sonner-toast]")).toHaveCount(0)
  }
  await setEnabled(true)
  await open(tokens.get("M")!, "/capacity?tab=team")
  await page.getByRole("button", { name: "调整", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByLabel("调整说明（选填）")).toHaveCount(0)
  await expect(dialog.locator("textarea")).toHaveCount(0)
  await dialog.getByLabel("其他工作占比（%）").fill("40")
  await dialog.getByRole("button", { name: "确认调整", exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByRole("row").filter({ hasText: "合成员工" }),
  ).toContainText("40%")
})
