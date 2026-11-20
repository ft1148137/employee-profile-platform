import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

for (const role of [
  { code: "R", department: "区域", total: 4, direct: 1, descendants: true },
  { code: "S", department: "门店甲", total: 2, direct: 2, descendants: false },
]) {
  test(`${role.department}经理查看本部门和含下级覆盖率`, async ({
    page,
    request,
  }) => {
    const password = "synthetic-coverage-password-2026"
    const token = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    expect(token.ok()).toBeTruthy()
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: (await token.json()).token,
        org_name: "合成区域覆盖率企业",
        admin_username: `coverage-${crypto.randomUUID()}@example.com`,
        admin_password: password,
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const admin = await initialized.json()
    const base = `/api/v1/orgs/${admin.org_id}`
    const headers = { Authorization: `Bearer ${admin.access_token}` }
    const roster = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,邮箱\nR,合成区域经理,区域,r-${crypto.randomUUID()}@example.com\nS,合成门店经理,区域/门店甲,s-${crypto.randomUUID()}@example.com\nA,合成甲店员工,区域/门店甲,a-${crypto.randomUUID()}@example.com\nB,合成乙店员工,区域/门店乙,b-${crypto.randomUUID()}@example.com\nO,合成范围外员工,外部,o-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
    expect(roster.ok()).toBeTruthy()
    const preview = await roster.json()
    expect(
      (
        await request.post(`${base}/roster/imports/${preview.id}/apply`, {
          headers,
          data: { base_revision: preview.base_revision },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.post(`${base}/accounts/issue`, { headers, data: {} })
      ).ok(),
    ).toBeTruthy()
    const accounts: EmployeeAccount[] = (
      await (await request.get(`${base}/accounts`, { headers })).json()
    ).data
    const manager = accounts.find((a) => a.employee_code === role.code)!
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
    const reset = await request.post(
      `${base}/accounts/${manager.account!.user_id}/reset-password`,
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
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: {
        Authorization: `Bearer ${(await login.json()).access_token}`,
      },
      data: {
        current_password: credentials.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    await page.goto("/login")
    await page.evaluate(
      ({ accessToken, orgId }) => {
        localStorage.setItem("access_token", accessToken)
        localStorage.setItem("current_org_id", orgId)
      },
      {
        accessToken: (await changed.json()).access_token,
        orgId: admin.org_id,
      },
    )
    await page.goto("/employees")
    await expect(
      page.getByRole("link", { name: "我的团队", exact: true }),
    ).toBeVisible()
    const coverage = page.getByRole("region", { name: "画像覆盖率" })
    await expect(
      coverage.getByText(`范围内在职员工 ${role.total} 人`),
    ).toBeVisible()
    await coverage.getByRole("button", { name: "按部门分布" }).click()
    if (role.descendants) {
      await expect(
        coverage.getByText(
          "含下级部门：4 人 · 已发布 0 · 存在争议 0 · 待本人确认 0 · 待审批 0 · 未知 4",
          { exact: true },
        ),
      ).toBeVisible()
    } else {
      await expect(coverage.getByText(/含下级部门：/)).toHaveCount(0)
      await expect(
        coverage.getByRole("button", { name: /^区域 ·/ }),
      ).toHaveCount(0)
      await expect(
        coverage.getByRole("button", { name: /^门店乙 ·/ }),
      ).toHaveCount(0)
    }
    await coverage
      .getByRole("button", {
        name: `${role.department} · ${role.direct} 人`,
        exact: true,
      })
      .click()
    await expect(page.locator("tbody tr")).toHaveCount(role.direct)
    await expect(
      coverage.getByText(`范围内在职员工 ${role.total} 人`),
    ).toBeVisible()
  })
}
