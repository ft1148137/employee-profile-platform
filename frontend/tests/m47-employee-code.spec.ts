import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

const isolated = test.extend<{
  company: {
    orgId: string
    adminToken: string
    managerToken: string
    employeeToken: string
    employees: EmployeeAccount[]
  }
}>({
  company: async ({ request }, use) => {
    const token = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    expect(token.ok()).toBeTruthy()
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: (await token.json()).token,
        org_name: "合成工号核对企业",
        admin_username: `code-admin-${crypto.randomUUID()}@example.com`,
        admin_password: "synthetic-password-2026",
        admin_full_name: "合成管理员",
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const admin = await initialized.json()
    const base = `/api/v1/orgs/${admin.org_id}`
    const headers = { Authorization: `Bearer ${admin.access_token}` }
    const uploaded = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,门店,经理,,m-${crypto.randomUUID()}@example.com\nHL025,王静,门店,店员,M,a-${crypto.randomUUID()}@example.com\nHL026,王静,门店,店员,M,b-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
    expect(uploaded.ok()).toBeTruthy()
    const roster = await uploaded.json()
    expect(
      (
        await request.post(`${base}/roster/imports/${roster.id}/apply`, {
          headers,
          data: { base_revision: roster.base_revision },
        })
      ).ok(),
    ).toBeTruthy()
    const issued = await request.post(`${base}/accounts/issue`, {
      headers,
      data: {},
    })
    expect(issued.ok()).toBeTruthy()
    const accounts: EmployeeAccount[] = (
      await (await request.get(`${base}/accounts`, { headers })).json()
    ).data
    const manager = accounts.find((row) => row.employee_code === "M")!
    expect(
      (
        await request.post(`${base}/role-grants`, {
          headers,
          data: {
            user_id: manager.account!.user_id,
            role: "dept_manager",
            department_id: manager.department!.id,
          },
        })
      ).ok(),
    ).toBeTruthy()
    const employees = ["HL025", "HL026"].map(
      (code) => accounts.find((row) => row.employee_code === code)!,
    )
    const tokens: string[] = []
    for (const account of [manager, employees[0]]) {
      const reset = await request.post(
        `${base}/accounts/${account.account!.user_id}/reset-password`,
        { headers },
      )
      expect(reset.ok()).toBeTruthy()
      const credentials = await reset.json()
      const login = await request.post("/api/v1/login/access-token", {
        form: {
          username: credentials.username,
          password: credentials.initial_password,
        },
      })
      expect(login.ok()).toBeTruthy()
      const { access_token } = await login.json()
      expect(
        (
          await request.patch("/api/v1/users/me/password", {
            headers: { Authorization: `Bearer ${access_token}` },
            data: {
              current_password: credentials.initial_password,
              new_password: "synthetic-password-2026",
            },
          })
        ).ok(),
      ).toBeTruthy()
      const refreshed = await request.post("/api/v1/login/access-token", {
        form: {
          username: credentials.username,
          password: "synthetic-password-2026",
        },
      })
      expect(refreshed.ok()).toBeTruthy()
      tokens.push((await refreshed.json()).access_token)
    }
    await use({
      orgId: admin.org_id,
      adminToken: admin.access_token,
      managerToken: tokens[0],
      employeeToken: tokens[1],
      employees,
    })
  },
})

for (const width of [1440, 390]) {
  isolated(
    `same-name teammates can be identified at ${width}px`,
    async ({ page, company }, testInfo) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto("/login")
      await page.evaluate(({ orgId, managerToken }) => {
        localStorage.setItem("access_token", managerToken)
        localStorage.setItem("current_org_id", orgId)
      }, company)
      await page.goto("/employees")
      for (const code of ["HL025", "HL026"]) {
        const employee = company.employees.find(
          (row) => row.employee_code === code,
        )!
        const row =
          width === 390
            ? page.getByRole("article").filter({ hasText: code })
            : page.getByRole("row").filter({ hasText: code })
        await expect(row).toContainText("王静")
        await expect(row).toBeVisible()
        await row.getByRole("link", { name: "王静", exact: true }).click()
        await expect(page).toHaveURL(new RegExp(`/employees/${employee.id}$`))
        const title = page.locator("header").filter({
          has: page.getByRole("heading", { name: "王静", exact: true }),
        })
        await expect(title).toContainText(`工号 ${employee.employee_code}`)
        await page.screenshot({
          path: testInfo.outputPath(`${employee.employee_code}-${width}.png`),
        })
        await page.goto("/employees")
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width)
    },
  )
}

isolated(
  "own records and administrator profile title show employee code",
  async ({ page, company }) => {
    await page.goto("/login")
    await page.evaluate(({ orgId, employeeToken }) => {
      localStorage.setItem("access_token", employeeToken)
      localStorage.setItem("current_org_id", orgId)
    }, company)
    await page.goto("/settings")
    await page.getByRole("tab", { name: "档案", exact: true }).click()
    const info = page.getByRole("region", { name: "员工信息" })
    await expect(info).toContainText("工号")
    await expect(info).toContainText("HL025")
    await page.evaluate((token) => {
      localStorage.setItem("access_token", token)
    }, company.adminToken)
    await page.goto(`/employees/${company.employees[1].id}`)
    await expect(
      page.locator("header").filter({
        has: page.getByRole("heading", { name: "王静", exact: true }),
      }),
    ).toContainText("工号 HL026")
  },
)
