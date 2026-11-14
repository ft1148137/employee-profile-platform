import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("departed employees leave the team and administrators see a read-only archive", async ({
  page,
  request,
}) => {
  const password = "synthetic-offboard-password-2026"
  const adminEmail = `admin-${crypto.randomUUID()}@example.com`
  const setupToken = await (
    await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
  ).json()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: setupToken.token,
      org_name: "合成离职归档企业",
      admin_username: adminEmail,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,邮箱\nM,合成经理,研发部,m-${crypto.randomUUID()}@example.com\nE,合成归档员工,研发部,e-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const roster = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${roster.id}/apply`, {
        headers,
        data: { base_revision: roster.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.post(`${base}/capabilities/versions`, {
        headers,
        data: {
          expected_version: 0,
          definitions: [
            {
              key: "synthetic_skill",
              name: "合成归档能力",
              category: "合成能力",
              description: "合成权限测试",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "合成等级",
                  },
                ],
              },
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const manager = accounts.find((a) => a.employee_code === "M")!
  const employee = accounts.find((a) => a.employee_code === "E")!
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
  expect(
    (
      await request.post(
        `${base}/employees/${employee.id}/profile/direct-edits`,
        {
          headers,
          data: {
            items: [
              {
                op: "add",
                capability_key: "synthetic_skill",
                statement: "合成归档判断",
                level: "routine",
              },
            ],
          },
        },
      )
    ).ok(),
  ).toBeTruthy()
  const reset = await (
    await request.post(
      `${base}/accounts/${manager.account!.user_id}/reset-password`,
      { headers },
    )
  ).json()
  const login = await (
    await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
  ).json()
  expect(
    (
      await request.patch("/api/v1/users/me/password", {
        headers: { Authorization: `Bearer ${login.access_token}` },
        data: {
          current_password: reset.initial_password,
          new_password: password,
        },
      })
    ).ok(),
  ).toBeTruthy()
  await logInUser(page, manager.account!.username, password)
  await page.goto("/employees")
  await expect(
    page.getByRole("row").filter({ hasText: "合成归档员工" }),
  ).toBeVisible()
  const previewResponse = await request.post(`${base}/roster/single-change`, {
    headers,
    data: { kind: "offboard", employee_ids: [employee.id] },
  })
  expect(previewResponse.ok()).toBeTruthy()
  const preview = await previewResponse.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: {
          base_revision: preview.base_revision,
          confirm_mass_deactivation: true,
        },
      })
    ).ok(),
  ).toBeTruthy()
  await page.reload()
  await expect(
    page.getByRole("row").filter({ hasText: "合成归档员工" }),
  ).toHaveCount(0)
  await page.goto(`/employees/${employee.id}`)
  await expect(page.getByRole("alert")).toContainText(
    "未找到该员工或你没有权限查看",
  )
  await logOutUser(page)
  await logInUser(page, adminEmail, password)
  await page.goto(`/employees/${employee.id}`)
  await expect(
    page.getByRole("heading", { name: "合成归档员工", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("已离职 · 归档只读", { exact: true }),
  ).toBeVisible()
  await expect(page.getByText("合成归档能力", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "添加能力", exact: true }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "直接修改", exact: true }),
  ).toHaveCount(0)
  await expect(page.getByRole("region", { name: "人员操作" })).toHaveCount(0)
})
