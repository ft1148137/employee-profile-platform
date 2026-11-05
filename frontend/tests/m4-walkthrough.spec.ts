import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("M4: employee update, manager publication, history and self-review denial", async ({
  page,
  request,
}, info) => {
  test.setTimeout(120000)
  const password = "synthetic-m4-password-2026"
  const setup = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setup.json()).token,
      org_name: "合成验收企业",
      admin_username: `m4-${crypto.randomUUID()}@example.com`,
      admin_password: password,
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
          `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,研发部,经理,,m-${crypto.randomUUID()}@example.com\nE,合成员工,研发部,工程师,M,e-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
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
              key: "skill",
              name: "合成能力",
              category: "技术",
              description: "验收能力",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "独立完成",
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
  const owner = accounts.find((a) => a.employee_code === "E")!
  const manager = accounts.find((a) => a.employee_code === "M")!
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
    const access = await (
      await request.post("/api/v1/login/access-token", {
        form: {
          username: reset.username,
          password: reset.initial_password,
        },
      })
    ).json()
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${access.access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    return (await changed.json()).access_token as string
  }
  const ownerToken = await login(owner)
  const managerToken = await login(manager)
  const ownerHeaders = { Authorization: `Bearer ${ownerToken}` }
  const managerHeaders = { Authorization: `Bearer ${managerToken}` }
  const enter = async (token: string, path: string) => {
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
  const first = await (
    await request.post(`${base}/me/requests`, {
      headers: ownerHeaders,
      data: {
        items: [
          {
            op: "add",
            capability_key: "skill",
            statement: "合成初始判断",
            owner_statement: "本人确认的初始依据",
          },
        ],
      },
    })
  ).json()
  const submitted = await (
    await request.post(`${base}/me/requests/${first.id}/submit`, {
      headers: ownerHeaders,
      data: { revision: first.revision },
    })
  ).json()
  expect(
    (
      await request.post(`${base}/reviews/${first.id}/decisions`, {
        headers: managerHeaders,
        data: {
          revision: submitted.revision,
          decisions: [{ item_id: submitted.items[0].id, decision: "approved" }],
        },
      })
    ).ok(),
  ).toBeTruthy()
  const draft = await (
    await request.post(`${base}/me/requests`, {
      headers: ownerHeaders,
      data: {
        items: [
          {
            op: "modify",
            capability_key: "skill",
            statement: "独立完成了合成项目验证",
            owner_statement: "本人确认的新增经历",
            level: "routine",
          },
        ],
      },
    })
  ).json()
  await enter(ownerToken, `/employees/${owner.id}`)
  await page
    .locator(`[id="request-${draft.id}"]`)
    .getByRole("button", { name: "继续修改" })
    .click()
  const editor = page.getByRole("dialog")
  await editor.getByRole("button", { name: "预览并提交", exact: true }).click()
  await editor.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(editor).toBeHidden()
  await enter(managerToken, `/reviews/${draft.id}`)
  await expect(page.getByRole("heading", { name: "逐项审批" })).toBeVisible()
  await expect(
    page.getByText("独立完成了合成项目验证", { exact: true }),
  ).toBeVisible()
  await page.getByLabel("合成能力的决定").selectOption("approved")
  await expectChineseCopy(page)
  await page.screenshot({
    path: info.outputPath("m4-manager-review.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "核对并提交" }).click()
  await page.getByRole("button", { name: "确认提交决定" }).click()
  await expect(page.getByRole("status")).toContainText("审批已提交")
  await enter(ownerToken, `/employees/${owner.id}`)
  await page.setViewportSize({ width: 390, height: 844 })
  const card = page.locator(`[id="request-${draft.id}"]`)
  await expect(card).toContainText("合成能力：批准")
  await card.getByText("请求时间线", { exact: true }).click()
  await expect(card.getByRole("list", { name: "请求时间线" })).toContainText(
    "发布画像 · r2",
  )
  await expectChineseCopy(page)
  await page.screenshot({
    path: info.outputPath("m4-employee-timeline.png"),
    fullPage: true,
  })
  await page.getByRole("tab", { name: "版本历史" }).click()
  await expect(page.getByRole("tabpanel")).toContainText("r2")
  await expect(page.getByRole("tabpanel")).toContainText("r1")
  const self = await (
    await request.post(`${base}/me/requests`, {
      headers: managerHeaders,
      data: {
        items: [
          {
            op: "add",
            capability_key: "skill",
            statement: "经理本人的合成能力",
            owner_statement: "合成依据",
          },
        ],
      },
    })
  ).json()
  const sent = await (
    await request.post(`${base}/me/requests/${self.id}/submit`, {
      headers: managerHeaders,
      data: { revision: self.revision },
    })
  ).json()
  expect(
    (
      await request.post(`${base}/reviews/${self.id}/decisions`, {
        headers: managerHeaders,
        data: {
          revision: sent.revision,
          decisions: [{ item_id: sent.items[0].id, decision: "approved" }],
        },
      })
    ).status(),
  ).toBe(403)
  expect(
    (await request.get(`${base}/reviews/${self.id}`, { headers })).ok(),
  ).toBeTruthy()
})
