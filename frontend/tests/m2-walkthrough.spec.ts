import path from "node:path"
import { configureModel, expect, test } from "./utils/model"
import { createSourcesOrg, saveCloud, syncJira } from "./utils/sources"
import { logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("M2: roster, Jira and PLM, identities, model, employee evidence", async ({
  page,
  modelUrl,
}, info) => {
  test.setTimeout(180000)
  await createSourcesOrg(page)
  const step = (name: RegExp) =>
    page
      .getByRole("navigation", { name: "设置步骤" })
      .getByRole("button", { name })
  const next = () =>
    page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await step(/企业信息/).click()
  await next()
  await page
    .getByLabel("拖拽名单到这里，或选择文件上传")
    .setInputFiles(
      path.resolve(
        import.meta.dirname,
        "../../examples/test-enterprises/qiguang-aiot/roster.csv",
      ),
    )
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  await next()
  await page.getByRole("button", { name: /智能硬件/ }).click()
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText("词典已启用，可以完成此步骤。")).toBeVisible()
  await next()
  await page.getByRole("button", { name: "生成账号", exact: true }).click()
  await page.getByRole("button", { name: "确认生成并下载" }).click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await expect(page.getByText("还有 0 名在职员工没有账号")).toBeVisible()
  await next()
  await saveCloud(
    page,
    "http://mock-jira:8080/qiguang-aiot",
    "jira-admin@example.com",
    "mock-cloud-token",
  )
  await syncJira(page)
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
  await next()
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
  await next()
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await page.getByRole("button", { name: "生成初始画像", exact: true }).click()
  await expect(
    page.getByText(
      "已为 26 名员工生成初始画像，员工登录后能在自己的画像页看到",
    ),
  ).toBeVisible({ timeout: 60000 })
  await next()
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible()
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
  await expect(page).toHaveURL("/inbox")
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
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
})
