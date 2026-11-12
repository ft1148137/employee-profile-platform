import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin home shows status and appeal todo through resubmission", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const token = await (
    await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
  ).json()
  const admin = await (
    await request.post("/api/v1/setup/initialize", {
      data: {
        token: token.token,
        org_name: "合成申诉企业",
        admin_username: `appeals-${crypto.randomUUID()}@example.com`,
        admin_password: "synthetic-appeal-password-2026",
        admin_full_name: "合成管理员",
      },
    })
  ).json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const preview = await (
    await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,职位,上级工号,邮箱\nE,合成员工,研发部,工程师,,appeal-owner-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
  ).json()
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
              key: "skill",
              name: "合成能力",
              category: "合成能力",
              description: "申诉测试",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "能独立完成合成任务",
                  },
                ],
              },
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  await request.post(`${base}/accounts/issue`, { headers, data: {} })
  const subject = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data[0]
  const reset = await (
    await request.post(
      `${base}/accounts/${subject.account.user_id}/reset-password`,
      { headers },
    )
  ).json()
  const login = await (
    await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
  ).json()
  const owner = await (
    await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${login.access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: "synthetic-owner-password-2026",
      },
    })
  ).json()
  const ownerHeaders = { Authorization: `Bearer ${owner.access_token}` }
  const draft = await (
    await request.post(`${base}/me/requests`, {
      headers: ownerHeaders,
      data: {
        items: [
          {
            op: "add",
            capability_key: "skill",
            statement: "合成的工作能力",
            owner_statement: "本人确认的合成依据",
          },
        ],
      },
    })
  ).json()
  const submitted = await (
    await request.post(`${base}/me/requests/${draft.id}/submit`, {
      headers: ownerHeaders,
      data: { revision: draft.revision },
    })
  ).json()
  expect(
    (
      await request.post(`${base}/reviews/${draft.id}/decisions`, {
        headers,
        data: {
          revision: submitted.revision,
          decisions: [
            {
              item_id: draft.items[0].id,
              decision: "rejected",
              reason: "请补充合成依据",
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  const enter = async (accessToken: string, path: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ accessToken, orgId }) => {
        localStorage.setItem("access_token", accessToken)
        localStorage.setItem("current_org_id", orgId)
      },
      { accessToken, orgId: admin.org_id },
    )
    await page.goto(path)
  }
  await enter(owner.access_token, `/employees/${subject.id}`)
  await page.getByRole("button", { name: "申诉", exact: true }).click()
  await expect(page.getByRole("button", { name: "提交申诉" })).toBeDisabled()
  await page.getByLabel("申诉原因").fill("本人希望补充独立完成的合成依据")
  await page.getByRole("button", { name: "提交申诉" }).click()
  await expect(page.getByText("申诉：待处理")).toBeVisible()
  await expect(
    page.getByRole("article", { name: "合成能力", exact: true }),
  ).toContainText("存在争议")
  await expectChineseCopy(page)
  const grant = await request.post(`${base}/role-grants`, {
    headers,
    data: { user_id: subject.account.user_id, role: "org_admin" },
  })
  expect(grant.ok()).toBeTruthy()
  await enter(owner.access_token, "/admin")
  await expect(
    page.getByRole("region", { name: "今天要处理的事" }),
  ).toContainText("1 个争议待处理")
  await page.goto(`/employees/${subject.id}?tab=appeals`)
  await expect(
    page.getByText("不能处理自己提出的申诉，请由另一位企业管理员处理"),
  ).toBeVisible()
  for (const name of ["维持原决定", "允许重新提交"]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0)
  }
  expect(
    (
      await request.delete(`${base}/role-grants/${(await grant.json()).id}`, {
        headers,
      })
    ).ok(),
  ).toBeTruthy()
  await enter(admin.access_token, "/admin")
  const today = page.getByRole("region", { name: "今天要处理的事" })
  for (const name of ["飞书", "AI 模型", "数据同步", "自动起草"]) {
    await expect(
      today.getByRole("article", { name, exact: true }),
    ).toBeVisible()
  }
  await expect(today).not.toContainText(/[A-Za-z]{3,}/)
  await page.screenshot({ path: "test-results/admin-home.png", fullPage: true })
  await today
    .getByRole("listitem")
    .filter({ hasText: "1 个争议待处理" })
    .getByRole("link", { name: "去处理" })
    .click()
  await expect(page).toHaveURL("/employees?filter=appeals")
  await expect(
    page.getByRole("row").filter({ hasText: subject.display_name }),
  ).toBeVisible()
  await expect(page.locator("tbody tr")).toHaveCount(1)
  await page.goto(`/employees/${subject.id}?tab=appeals`)
  await expect(
    page.getByRole("heading", { name: "争议", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("申诉原因：本人希望补充独立完成的合成依据"),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "允许重新提交" }),
  ).toBeDisabled()
  await page.getByLabel("处理说明").fill("请在新草稿补充合成依据")
  await expectChineseCopy(page)
  await page.getByRole("button", { name: "允许重新提交" }).click()
  await expect(page.getByRole("status")).toContainText("0 项待处理申诉")
  await page.goto("/admin")
  await expect(
    page.getByRole("region", { name: "今天要处理的事" }),
  ).not.toContainText("个争议待处理")
  await page.route("**/admin/overview", async (route) => {
    const response = await route.fetch()
    await route.fulfill({ json: { ...(await response.json()), todos: [] } })
  })
  await page.reload()
  await expect(page.getByText("今天没有要处理的事")).toBeVisible()
  await page.unroute("**/admin/overview")
  await enter(owner.access_token, "/inbox")
  await expect(
    page.getByText("申诉已处理：允许重新提交，请修改草稿"),
  ).toBeVisible()
  await page.goto(`/employees/${subject.id}`)
  await expect(
    page.getByText("申诉：允许重新提交 · 请在新草稿补充合成依据"),
  ).toBeVisible()
  await expect(
    page.getByRole("article", { name: "合成能力", exact: true }),
  ).not.toContainText("存在争议")
  await page.getByRole("button", { name: "继续修改" }).click()
  await page
    .getByRole("dialog")
    .getByRole("textbox", { name: "依据说明", exact: true })
    .fill("本人补充了独立完成的合成依据")
  await page.getByRole("button", { name: "预览并提交", exact: true }).click()
  await page.getByRole("button", { name: "确认提交", exact: true }).click()
  await expect(page.getByRole("dialog")).toBeHidden()
  await expect(page.getByText("待经理审批", { exact: false })).toBeVisible()
})
