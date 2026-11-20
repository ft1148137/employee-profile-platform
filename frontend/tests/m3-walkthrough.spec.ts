import { readFileSync } from "node:fs"
import path from "node:path"
import { configureModel, expect, jiraTestUrl, test } from "./utils/model"
import { importRoster } from "./utils/roster"
import {
  activateTemplate,
  addDraftingEvidence,
  createSourcesOrg,
  saveCloud,
  syncJira,
} from "./utils/sources"
import { logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15000 })

test("M3: setup, mandatory model, mobile form and bot", async ({
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
          "../../examples/test-enterprises/qingli-saas/roster.csv",
        ),
        "utf8",
      ).replace(/@/g, `+${crypto.randomUUID()}@`),
    ),
  )
  await activateTemplate(page, "tech-rnd")
  await page.goto("/admin/people?tab=accounts")
  await page.getByRole("button", { name: "发放账号", exact: true }).click()
  await page.getByRole("button", { name: "确认生成并下载" }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await page.goto("/admin/data-ai?tab=sources")
  await saveCloud(
    page,
    new URL("/qingli-saas", jiraTestUrl).href,
    "jira-admin@example.com",
    "mock-cloud-token",
  )
  await syncJira(page)
  await page.screenshot({
    path: info.outputPath("sources.png"),
    animations: "disabled",
    fullPage: true,
  })
  await page.goto("/admin/people?tab=identities")
  const boxes = page.getByRole("checkbox", { name: /选择账号/ })
  await boxes.first().waitFor()
  for (const box of await boxes.all()) {
    if (await box.isEnabled()) await box.check()
  }
  const link = page.getByRole("button", { name: /确认所选候选/ })
  if (await link.isEnabled()) await link.click()
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
    (row: { employee_code: string }) => row.employee_code === "QL003",
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
  await page.setViewportSize({ width: 390, height: 844 })
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
  await initialItem.getByRole("button", { name: "修改", exact: true }).click()
  await expect(
    page.getByRole("dialog").getByRole("heading", { name: "更新我的画像" }),
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("employee-profile-editor.png"),
    animations: "disabled",
    fullPage: true,
  })
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .click()
  await page
    .getByRole("region", { name: "初始画像草稿", exact: true })
    .getByRole("button", { name: "修改", exact: true })
    .first()
    .click()
  const editor = page.getByRole("dialog")
  await editor.getByLabel("新增能力").selectOption("automated_testing")
  await editor
    .getByRole("textbox", { name: "能力说明", exact: true })
    .last()
    .fill("我补充了一段合成项目经历")
  await editor.getByLabel("补充说明").last().fill("本人完成了合成项目的验证")
  await editor.getByRole("button", { name: "预览并提交", exact: true }).click()
  await expect(editor.getByRole("heading", { name: "变更预览" })).toBeVisible()
  await editor.getByRole("button", { name: "确认提交", exact: true }).click()
  const pending = page.getByRole("region", { name: "其他待处理请求" })
  await expect(pending).toContainText("待经理审批")
  page.once("dialog", (dialog) => dialog.accept())
  await pending
    .getByRole("button", { name: "撤回", exact: true })
    .first()
    .click()
  await expect(pending).not.toBeVisible()
  await expect(page.getByRole("region", { name: "已处理请求" })).toContainText(
    "已撤回",
  )
  await page.getByRole("link", { name: /^协同/ }).click()
  const bot = page.getByRole("region", { name: "助手", exact: true })
  await bot.getByLabel("发给助手的消息").fill("补充一段合成项目经历")
  await bot.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    bot.getByText("本人完成了合成项目的验证", { exact: false }),
  ).toHaveCount(0)
  await expect(
    bot.getByText("我补充了一段合成项目经历", { exact: false }),
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("m3-mobile-bot-preview.png"),
    animations: "disabled",
  })
  await bot
    .getByRole("button", { name: "确认提交", exact: true })
    .last()
    .click()
  await expect(bot.getByText("已提交审批，经理审批前可撤回。")).toBeVisible()
  await bot.getByRole("button", { name: "撤回", exact: true }).click()
  await expect(bot.getByRole("heading", { name: "已撤回" })).toBeVisible()
})
