import { expect, type Page, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

const password = "synthetic-direct-password-2026"

const isolated = test.extend<{
  company: {
    employee: EmployeeAccount
    manager: EmployeeAccount
    outsider: EmployeeAccount
    adminEmail: string
  }
}>({
  company: async ({ request }, use) => {
    const adminEmail = `admin-${crypto.randomUUID()}@example.com`
    const token = await (
      await request.post("/api/v1/private/setup-tokens", {
        data: { purpose: "new_org" },
      })
    ).json()
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: token.token,
        org_name: "合成编辑企业",
        admin_username: adminEmail,
        admin_password: password,
        admin_full_name: "合成管理员",
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const admin = await initialized.json()
    const base = `/api/v1/orgs/${admin.org_id}`
    const headers = { Authorization: `Bearer ${admin.access_token}` }
    const roster = await (
      await request.post(`${base}/roster/imports`, {
        headers,
        multipart: {
          file: {
            name: "roster.csv",
            mimeType: "text/csv",
            buffer: Buffer.from(
              `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,研发部,经理,,m-${crypto.randomUUID()}@example.com\nE,合成员工,研发部,工程师,M,e-${crypto.randomUUID()}@example.com\nO,范围外员工,销售部,销售,,o-${crypto.randomUUID()}@example.com\n`,
            ),
          },
        },
      })
    ).json()
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
            definitions: [1, 2].map((n) => ({
              key: `skill_${n}`,
              name: `合成能力${n}`,
              category: "合成能力",
              description: "界面编辑测试",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "独立完成",
                  },
                  {
                    key: "advanced",
                    label: "处理复杂任务",
                    description: "处理复杂任务",
                  },
                ],
              },
            })),
          },
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
    const employee = accounts.find((a) => a.employee_code === "E")!
    const manager = accounts.find((a) => a.employee_code === "M")!
    const outsider = accounts.find((a) => a.employee_code === "O")!
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
    for (const account of [employee, manager]) {
      const reset = await (
        await request.post(
          `${base}/accounts/${account.account!.user_id}/reset-password`,
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
    }
    expect(
      (
        await request.post(
          `${base}/employees/${employee.id}/profile/direct-edits`,
          {
            headers,
            data: {
              base_revision: null,
              items: [
                {
                  capability_key: "skill_1",
                  op: "add",
                  statement: "原始合成陈述",
                  level: "routine",
                },
              ],
            },
          },
        )
      ).ok(),
    ).toBeTruthy()
    await use({ employee, manager, outsider, adminEmail })
  },
})

async function chooseAddition(page: Page) {
  await page.getByRole("button", { name: "添加能力", exact: true }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "合成能力2", exact: true })
    .click()
}

isolated(
  "owner manually submits a capability for review and hands existing or new skills to the assistant",
  async ({ page, company }) => {
    await logInUser(page, company.employee.account!.username, password)
    await page.goto(`/employees/${company.employee.id}`)
    const card = page.getByRole("article", { name: "合成能力1", exact: true })
    await card.getByRole("button", { name: "合成能力1", exact: true }).click()
    await page.getByRole("button", { name: "手动编辑", exact: true }).click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByRole("textbox", { name: "陈述", exact: true })
      .fill("本人补充的合成陈述")
    await dialog
      .getByRole("combobox", { name: /^等级/ })
      .selectOption("advanced")
    await dialog.getByRole("button", { name: "预览并提交" }).click()
    await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(
      page.getByRole("region", { name: "其他待处理请求" }),
    ).toContainText("待经理审批")
    await expect(card).toContainText("独立完成")
    for (const adding of [false, true]) {
      if (adding) await chooseAddition(page)
      else
        await card
          .getByRole("button", { name: "合成能力1", exact: true })
          .click()
      let sends = 0
      const countSend = (request: import("@playwright/test").Request) => {
        if (
          request.method() === "POST" &&
          /\/turns$/.test(new URL(request.url()).pathname)
        )
          sends++
      }
      page.on("request", countSend)
      await page.getByRole("link", { name: "让助手改", exact: true }).click()
      await expect(page).toHaveURL(/\/assistant\?prompt=/)
      await expect(page.getByPlaceholder("给画像机器人发消息…")).toHaveValue(
        `我想修改「合成能力${adding ? 2 : 1}」这项能力，现在的等级是${adding ? "未填写" : "独立完成"}：`,
      )
      await expect(
        page.getByRole("button", { name: "发送", exact: true }),
      ).toBeEnabled()
      expect(sends).toBe(0)
      page.off("request", countSend)
      await page.goto(`/employees/${company.employee.id}`)
    }
  },
)

for (const role of ["manager", "admin"] as const) {
  isolated(
    `${role} directly modifies, adds and retracts skills and notifies the owner`,
    async ({ page, company }) => {
      await logInUser(
        page,
        role === "manager"
          ? company.manager.account!.username
          : company.adminEmail,
        password,
      )
      await page.goto(`/employees/${company.employee.id}`)
      const card = page.getByRole("article", { name: "合成能力1", exact: true })
      await card.getByRole("button", { name: "直接修改", exact: true }).click()
      const dialog = page.getByRole("dialog")
      await dialog
        .getByRole("textbox", { name: "陈述", exact: true })
        .fill("直接更新的合成陈述")
      await dialog
        .getByRole("combobox", { name: /^等级/ })
        .selectOption("advanced")
      await dialog.getByRole("button", { name: "预览修改" }).click()
      await expect(dialog).toContainText("直接发布，不需要本人同意，会通知本人")
      await expect(
        dialog.getByRole("region", { name: "修改前" }),
      ).toContainText("独立完成")
      await expect(
        dialog.getByRole("region", { name: "修改后" }),
      ).toContainText("处理复杂任务")
      await dialog.getByRole("button", { name: "确认直接发布" }).click()
      await expect(dialog).toBeHidden()
      await expect(card).toContainText("处理复杂任务")
      await expect(card).toContainText("已确认")
      await chooseAddition(page)
      await dialog
        .getByRole("textbox", { name: "陈述", exact: true })
        .fill("新增的合成陈述")
      await dialog
        .getByRole("combobox", { name: /^等级/ })
        .selectOption("routine")
      await dialog.getByRole("button", { name: "预览修改" }).click()
      await dialog.getByRole("button", { name: "确认直接发布" }).click()
      await expect(dialog).toBeHidden()
      const added = page.getByRole("article", {
        name: "合成能力2",
        exact: true,
      })
      await expect(added).toContainText("已确认")
      await added.getByRole("button", { name: "直接修改", exact: true }).click()
      await dialog.getByLabel("撤回这条判断", { exact: true }).check()
      await dialog.getByRole("button", { name: "预览修改" }).click()
      await expect(
        dialog.getByRole("region", { name: "修改后" }),
      ).toContainText("未知")
      await dialog.getByRole("button", { name: "确认直接发布" }).click()
      await expect(dialog).toBeHidden()
      await page.reload()
      await expect(
        page.getByRole("article").filter({ hasText: "新增的合成陈述" }),
      ).toHaveCount(0)
      if (role === "manager") {
        await page.goto(`/employees/${company.manager.id}`)
        await expect(
          page.getByRole("heading", { name: "合成经理", exact: true }),
        ).toBeVisible()
        await expect(
          page.getByRole("button", { name: "直接修改", exact: true }),
        ).toHaveCount(0)
        await page.goto(`/employees/${company.outsider.id}`)
        await expect(page.getByRole("alert")).toContainText(
          "未找到该员工或你没有权限查看",
        )
      }
      await logOutUser(page)
      await logInUser(page, company.employee.account!.username, password)
      await page.goto("/inbox")
      await expect(
        page.getByText("画像被直接修改", { exact: true }),
      ).toHaveCount(4)
      await expect(
        page.getByText(/直接修改了你的画像：合成能力1/).first(),
      ).toContainText(role === "manager" ? "合成经理" : "合成管理员")
    },
  )
}

isolated(
  "stale versions block both preview and publication until the profile is refreshed",
  async ({ page, company }) => {
    await logInUser(page, company.manager.account!.username, password)
    await page.goto(`/employees/${company.employee.id}`)
    const path = `**/employees/${company.employee.id}/profile/direct-edits`
    for (const stage of ["preview", "publish"]) {
      await page
        .getByRole("article", { name: "合成能力1", exact: true })
        .getByRole("button", { name: "直接修改", exact: true })
        .click()
      const dialog = page.getByRole("dialog")
      const target = stage === "preview" ? `${path}/preview` : path
      await page.route(target, (route) => {
        expect(route.request().postDataJSON().base_revision).toBe(1)
        return route.fulfill({
          status: 409,
          json: { detail: "画像已变化，请重新查看" },
        })
      })
      await dialog.getByRole("button", { name: "预览修改" }).click()
      if (stage === "publish")
        await dialog.getByRole("button", { name: "确认直接发布" }).click()
      await expect(dialog.getByRole("alert")).toHaveText(
        "画像已变化，请刷新后再改",
      )
      await expect(
        dialog.getByRole("button", { name: "确认直接发布" }),
      ).toHaveCount(0)
      await dialog.getByRole("button", { name: "刷新画像" }).click()
      await expect(dialog).toBeHidden()
      await page.unroute(target)
    }
  },
)
