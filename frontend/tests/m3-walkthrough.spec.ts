import { readFileSync } from "node:fs"
import path from "node:path"
import { configureModel, expect, test } from "./utils/model"
import { importRoster } from "./utils/roster"
import {
  activateTemplate,
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
  await page.getByRole("button", { name: "生成账号", exact: true }).click()
  await page.getByRole("button", { name: "确认生成并下载" }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await page.goto("/admin/data-ai?tab=sources")
  await saveCloud(
    page,
    "http://mock-jira:8080/qingli-saas",
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
  await page.goto("/admin/assistant")
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("模型")
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成模型配置", exact: true }).click()
  await page.goto("/admin/rules?tab=approval")
  await expect(
    page.getByRole("tab", { name: "审批规则", exact: true, selected: true }),
  ).toBeVisible()
  await expect(page.getByLabel("全企业需要经理确认")).toBeChecked()
  await expect(page.getByLabel("员工过目期限（天）")).toHaveValue("14")
  await page.goto("/admin/data-ai?tab=model")
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
  await expect(page).toHaveURL("/inbox")
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(
    page.getByRole("heading", { name: "待你过目：系统起草的初始画像" }),
  ).toBeVisible()
  await page
    .getByRole("region", { name: /^草稿改动：/ })
    .first()
    .getByRole("button")
    .first()
    .click()
  await expect(page.getByRole("dialog")).toContainText("Jira 记录")
  await expect(page.getByRole("dialog")).toContainText("有效")
  await page.screenshot({
    path: info.outputPath("employee-evidence.png"),
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
    .getByRole("textbox", { name: "陈述", exact: true })
    .last()
    .fill("我补充了一段合成项目经历")
  await editor.getByLabel("依据说明").last().fill("本人完成了合成项目的验证")
  await editor.getByRole("button", { name: "预览并提交", exact: true }).click()
  await expect(
    editor.getByRole("heading", { name: "提交前预览" }),
  ).toBeVisible()
  await editor.getByRole("button", { name: "确认提交", exact: true }).click()
  const pending = page.getByRole("region", { name: "其他待处理请求" })
  await expect(pending).toContainText("待经理审批")
  await pending
    .getByRole("button", { name: "撤回", exact: true })
    .first()
    .click()
  await page.getByRole("button", { name: "打开画像机器人" }).click()
  const bot = page.getByRole("dialog", { name: "我的画像机器人" })
  await bot.getByLabel("发给机器人的消息").fill("补充一段合成项目经历")
  await bot.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    bot.getByText("本人完成了合成项目的验证", { exact: false }),
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("m3-mobile-bot-preview.png"),
    animations: "disabled",
  })
  await bot
    .getByRole("button", { name: "确认提交", exact: true })
    .last()
    .click()
  await expect(
    bot.getByText("已提交给经理审批，审批前可以撤回。"),
  ).toBeVisible()
  await bot.getByRole("button", { name: "撤回", exact: true }).click()
  await expect(bot.getByRole("heading", { name: "已撤回" })).toBeVisible()
})
