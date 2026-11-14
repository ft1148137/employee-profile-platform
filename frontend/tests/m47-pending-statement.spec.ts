import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("non-reviewer admin sees metadata and a privacy notice without the unpublished statement", async ({
  page,
  request,
}) => {
  const password = "synthetic-review-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成审批企业",
      admin_username: `review-${crypto.randomUUID()}@example.com`,
      admin_password: password,
      admin_full_name: "合成管理员",
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
          `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,研发部,经理,,manager-${crypto.randomUUID()}@example.com\nE,合成员工,研发部,工程师,M,employee-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
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
      await request.post(`${base}/capabilities/versions`, {
        headers,
        data: {
          expected_version: 0,
          definitions: [1].map((number) => ({
            key: `skill_${number}`,
            name: `合成能力${number}`,
            category: "合成能力",
            description: "审批测试",
            level_rubric: {
              levels: [
                {
                  key: "routine",
                  label: "独立完成",
                  description: "能独立完成",
                },
              ],
            },
          })),
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
  const subject = accounts.find((value) => value.employee_code === "E")!
  const manager = accounts.find((value) => value.employee_code === "M")!
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
  const login = async (account: EmployeeAccount) => {
    const reset = await (
      await request.post(
        `${base}/accounts/${account.account!.user_id}/reset-password`,
        { headers },
      )
    ).json()
    const result = await (
      await request.post("/api/v1/login/access-token", {
        form: { username: reset.username, password: reset.initial_password },
      })
    ).json()
    return (
      await (
        await request.patch("/api/v1/users/me/password", {
          headers: { Authorization: `Bearer ${result.access_token}` },
          data: {
            current_password: reset.initial_password,
            new_password: password,
          },
        })
      ).json()
    ).access_token
  }
  const ownerToken = await login(subject)

  const ownerHeaders = { Authorization: `Bearer ${ownerToken}` }
  const statement = "仅供本人和审批人查看的合成未发布陈述"
  const created = await request.post(`${base}/me/requests`, {
    headers: ownerHeaders,
    data: {
      items: [
        { op: "add", capability_key: "skill_1", statement, level: "routine" },
      ],
    },
  })
  expect(created.ok()).toBeTruthy()
  const draft = await created.json()
  expect(
    (
      await request.post(`${base}/me/requests/${draft.id}/submit`, {
        headers: ownerHeaders,
        data: { revision: draft.revision },
      })
    ).ok(),
  ).toBeTruthy()
  const response = await request.get(
    `${base}/employees/${subject.id}/change-requests?status=all`,
    { headers },
  )
  expect(response.ok()).toBeTruthy()
  expect(await response.text()).not.toContain(statement)
  await page.goto("/login")
  await page.evaluate(
    ({ accessToken, orgId }) => {
      localStorage.setItem("access_token", accessToken)
      localStorage.setItem("current_org_id", orgId)
    },
    { accessToken: admin.access_token, orgId: admin.org_id },
  )
  await page.goto(`/employees/${subject.id}`)
  const card = page.locator(`[id="request-${draft.id}"]`)
  await expect(card).toContainText("未发布的陈述仅本人、管辖经理和审批人可见")
  await expect(card).toContainText("合成能力1")
  await expect(card).toContainText("独立完成")
  await expect(page.locator("body")).not.toContainText(statement)
})
