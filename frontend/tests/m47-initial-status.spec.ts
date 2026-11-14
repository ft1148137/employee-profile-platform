import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

const password = "synthetic-direct-password-2026"

const isolated = test.extend<{
  company: {
    employee: EmployeeAccount
    manager: EmployeeAccount
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
        org_name: "合成初始状态企业",
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
    await use({ employee, manager, adminEmail })
  },
})

isolated(
  "supplement waits for its reviewer and direct publication does not imply owner review",
  async ({ page, company }) => {
    await logInUser(page, company.employee.account!.username, password)
    await page.goto(`/employees/${company.employee.id}`)
    await page.getByRole("button", { name: "添加能力", exact: true }).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("button", { name: "合成能力1", exact: true }).click()
    await dialog.getByRole("button", { name: "手动编辑", exact: true }).click()
    await dialog
      .getByRole("textbox", { name: "陈述", exact: true })
      .fill("本人补充的合成陈述")
    await dialog
      .getByRole("combobox", { name: /^等级/ })
      .selectOption("routine")
    await dialog
      .getByRole("button", { name: "预览并提交", exact: true })
      .click()
    await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(dialog).toBeHidden()
    await page.reload()
    const explanation = page.getByRole("region", { name: "画像说明" })
    await expect(explanation).toContainText(
      "你的补充正在等 合成经理（直属上级） 审批",
    )
    await expect(explanation).toContainText("企业还没有接入工作记录")
    await expect(explanation).not.toContainText("已提交、已发布或本人已过目")
    await expect(explanation).not.toContainText("初始画像已由你过目")
    await logOutUser(page)
    await logInUser(page, company.manager.account!.username, password)
    await page.goto(`/employees/${company.employee.id}`)
    await page.getByRole("button", { name: "添加能力", exact: true }).click()
    await dialog.getByRole("button", { name: "合成能力2", exact: true }).click()
    await dialog
      .getByRole("textbox", { name: "陈述", exact: true })
      .fill("经理直接发布的合成陈述")
    await dialog
      .getByRole("combobox", { name: /^等级/ })
      .selectOption("routine")
    await dialog.getByRole("button", { name: "预览修改", exact: true }).click()
    await dialog
      .getByRole("button", { name: "确认直接发布", exact: true })
      .click()
    await expect(dialog).toBeHidden()
    await page.goto("/employees")
    const row = page.getByRole("row").filter({ hasText: "合成员工" })
    await expect(row).toContainText("经理或管理员已直接修改")
    await expect(row).not.toContainText("本人已过目")
    await page.setViewportSize({ width: 390, height: 844 })
    const card = page
      .getByRole("region", { name: "团队员工卡片" })
      .getByRole("article")
      .filter({ hasText: "合成员工" })
    await expect(card).toContainText("经理或管理员已直接修改")
    await expect(card).not.toContainText("本人已过目")
  },
)
