import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("管理员审批最高负责人时看到分配原因", async ({ page, request }) => {
  const password = "synthetic-review-reason-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成审批原因企业",
      admin_username: `reason-${crypto.randomUUID()}@example.com`,
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
          `工号,姓名,部门,职位,上级工号,邮箱\nCEO,合成最高负责人,总经办,总经理,,ceo-${crypto.randomUUID()}@example.com\n`,
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
          definitions: [
            {
              key: "planning",
              name: "合成规划能力",
              category: "合成能力",
              description: "审批原因测试",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "能独立完成",
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
  const subject = accounts.find((value) => value.employee_code === "CEO")!
  const reset = await (
    await request.post(
      `${base}/accounts/${subject.account!.user_id}/reset-password`,
      { headers },
    )
  ).json()
  const login = await (
    await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
  ).json()
  const changed = await (
    await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${login.access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
  ).json()
  const ownerHeaders = { Authorization: `Bearer ${changed.access_token}` }
  const draft = await (
    await request.post(`${base}/me/requests`, {
      headers: ownerHeaders,
      data: {
        items: [
          {
            op: "add",
            capability_key: "planning",
            statement: "合成最高负责人申请",
            level: "routine",
          },
        ],
      },
    })
  ).json()
  expect(
    (
      await request.post(`${base}/me/requests/${draft.id}/submit`, {
        headers: ownerHeaders,
        data: { revision: draft.revision },
      })
    ).ok(),
  ).toBeTruthy()
  await page.goto("/login")
  await page.evaluate(
    ({ accessToken, orgId }) => {
      localStorage.setItem("access_token", accessToken)
      localStorage.setItem("current_org_id", orgId)
    },
    { accessToken: admin.access_token, orgId: admin.org_id },
  )
  await page.goto("/reviews")
  await page.locator(`a[href="/reviews/${draft.id}"]`).click()
  await expect(page.getByRole("heading", { name: "逐项审批" })).toBeVisible()
  await expect(
    page.getByText(
      "分配原因：申请人所在部门和上级部门都没有部门经理，由企业管理员审批",
      { exact: true },
    ),
  ).toBeVisible()
})
