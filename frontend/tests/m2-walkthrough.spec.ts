import { readFileSync } from "node:fs"
import path from "node:path"
import { configureModel, expect, test } from "./utils/model"
import { importRoster } from "./utils/roster"
import {
  activateTemplate,
  addDraftingEvidence,
  createSourcesOrg,
  saveCloud,
  syncJira,
} from "./utils/sources"
import { logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("M2: roster, Jira and PLM, identities, model, employee evidence", async ({
  page,
  modelUrl,
}, info) => {
  test.setTimeout(180000)
  await createSourcesOrg(page)
  await importRoster(
    page,
    Buffer.from(
      readFileSync(
        path.resolve(
          import.meta.dirname,
          "../../examples/test-enterprises/qiguang-aiot/roster.csv",
        ),
        "utf8",
      ).replace(/@/g, `+${crypto.randomUUID()}@`),
    ),
  )
  await activateTemplate(page, "aiot-hardware")
  await page.goto("/admin/people?tab=accounts")
  await page.getByRole("button", { name: "发放账号", exact: true }).click()
  await page.getByRole("button", { name: "确认生成并下载" }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await page.goto("/admin/data-ai?tab=sources")
  await page
    .getByRole("button", { name: "配置 Jira Cloud", exact: true })
    .click()
  await saveCloud(
    page,
    "http://mock-jira:8080/qiguang-aiot",
    "jira-admin@example.com",
    "mock-cloud-token",
  )
  await syncJira(page)
  await page.getByRole("button", { name: "关闭", exact: true }).click()
  await page
    .getByRole("button", { name: "导入 CSV / Excel 工作记录", exact: true })
    .click()
  await page
    .getByLabel("选择工作记录文件")
    .setInputFiles(
      path.resolve(
        import.meta.dirname,
        "../../examples/test-enterprises/qiguang-aiot/work_records.csv",
      ),
    )
  await expect(page.getByText("文件导入：成功", { exact: true })).toBeVisible({
    timeout: 60000,
  })
  await page.screenshot({
    path: info.outputPath("sources.png"),
    animations: "disabled",
    fullPage: true,
  })
  await page.goto("/admin/people?tab=identities")
  await page.getByLabel("按来源筛选").selectOption("")
  await page
    .getByRole("checkbox", { name: /选择账号/ })
    .first()
    .waitFor()
  for (const box of await page
    .getByRole("checkbox", { name: /选择账号/ })
    .all()) {
    if (await box.isEnabled()) await box.check()
  }
  await page.getByRole("button", { name: /确认所选候选/ }).click()
  await expect(page.getByRole("status")).toContainText("已补充")
  await page.goto("/")
  await expect(page).toHaveURL(/\/assistant\?mode=admin&session=/)
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("模型")
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await page.goto("/admin/rules?tab=approval")
  await expect(
    page.getByRole("link", { name: "审批规则", exact: true }),
  ).toHaveAttribute("aria-current", "page")
  await expect(page.getByLabel("默认需要经理审批")).toBeChecked()
  await expect(page.getByLabel("员工确认期限（天）")).toHaveValue("14")
  await page.goto("/admin/data-ai?tab=model")
  await addDraftingEvidence(page)
  page.once("dialog", (dialog) => dialog.accept())
  await page.getByRole("button", { name: "重新起草全部", exact: true }).click()
  await expect(
    page.getByText(
      "已为 26 名员工生成初始画像，员工登录后能在自己的画像页看到",
    ),
  ).toBeVisible({ timeout: 60000 })
  const { base, headers } = await page.evaluate(() => ({
    base: `/api/v1/orgs/${localStorage.getItem("current_org_id")}`,
    headers: {
      Authorization: `Bearer ${localStorage.getItem("access_token")}`,
    },
  }))
  const accounts = await (
    await page.request.get(`${base}/accounts?limit=100`, { headers })
  ).json()
  const account = accounts.data.find(
    (row: { employee_code: string }) => row.employee_code === "QG008",
  ).account
  const reset = await (
    await page.request.post(
      `${base}/accounts/${account.user_id}/reset-password`,
      { headers },
    )
  ).json()
  await logOutUser(page)
  await page.getByTestId("email-input").fill(account.username)
  await page.getByTestId("password-input").fill(reset.initial_password)
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await page
    .getByLabel("当前密码", { exact: true })
    .fill(reset.initial_password)
  await page
    .getByLabel("新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page
    .getByLabel("确认新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page.getByRole("button", { name: "保存密码" }).click()
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.locator("summary", { hasText: /待你确认：初始画像草稿/ }).click()
  await expect(
    page.getByRole("heading", { name: "待你确认：初始画像草稿" }),
  ).toBeVisible()
  const initialItem = page.getByRole("region", { name: /^草稿改动：/ }).first()
  await expect(initialItem.getByRole("heading")).toBeVisible()
  await expect(initialItem).toContainText("修改后：")
  await expect(initialItem.getByText("工作记录", { exact: false })).toHaveCount(
    0,
  )
  const employeeHeaders = await page.evaluate(() => ({
    Authorization: `Bearer ${localStorage.getItem("access_token")}`,
  }))
  const evidence = await page.request.get(
    `${base}/me/evidence?kind=work_record`,
    { headers: employeeHeaders },
  )
  expect(evidence.ok()).toBeTruthy()
  expect((await evidence.json()).data).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        kind: "work_record",
        source: "jira",
        status: "active",
      }),
    ]),
  )
  await page.screenshot({
    path: info.outputPath("employee-profile.png"),
    animations: "disabled",
    fullPage: true,
  })
})
