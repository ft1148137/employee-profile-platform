import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin uploads, previews and confirms a dictionary version", async ({
  page,
  request,
}) => {
  const email = `dictionary-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "词典合成测试企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expectChineseCopy(page, ["YAML", "AI"])
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "下载模板（Excel）" }).click()
  expect((await download).suggestedFilename()).toBe("能力词典模板.xlsx")
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  await page
    .getByLabel("上传 YAML 或 Excel 词典（不超过 2 MB）")
    .setInputFiles({
      name: "broken.yaml",
      mimeType: "application/yaml",
      buffer: Buffer.from("- key: custom\n"),
    })
  await expect(page.getByRole("alert")).toContainText("第 1 行")
  await expect(
    page.getByRole("button", { name: "确认保存新版本" }),
  ).toBeDisabled()
  const content =
    "- key: custom\n  name: 合成能力\n  category: 工程\n  description: 合成能力说明\n  ai_inference: false\n  level_rubric:\n    levels:\n      - key: basic\n        label: 基础\n        description: 在指导下完成\n"
  await page
    .getByLabel("上传 YAML 或 Excel 词典（不超过 2 MB）")
    .setInputFiles({
      name: "custom.yaml",
      mimeType: "application/yaml",
      buffer: Buffer.from(content),
    })
  await expect(
    page.getByRole("region", { name: "词典改动预览" }),
  ).toContainText("新增 1 项")
  await expect(
    page.getByRole("region", { name: "词典改动预览" }),
  ).toContainText("AI 起草列已不再使用，已忽略")
  await expectChineseCopy(page, ["YAML", "AI", "custom"])
  await page.getByRole("button", { name: "确认保存新版本" }).click()
  await expect(
    page.getByRole("status").filter({ hasText: "新词典已启用" }),
  ).toBeVisible()
  await expect(page.getByText("当前第 1 版 · 1 项能力")).toBeVisible()
  await page.reload()
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expect(page.getByText("当前第 1 版 · 1 项能力")).toBeVisible()
  const exported = page.waitForEvent("download")
  await page.getByRole("button", { name: "导出当前词典" }).click()
  expect((await exported).suggestedFilename()).toBe("本企业能力词典.yaml")
  await page.screenshot({
    path: "test-results/capability-dictionary.png",
    fullPage: true,
  })
})

for (const email of [
  "employee.platform@demo.example.com",
  "manager.platform@demo.example.com",
]) {
  test(`${email} can export but cannot edit`, async ({ page }) => {
    await logInUser(page, email, process.env.DEMO_USER_PASSWORD!)
    await page.goto("/admin/rules?tab=dictionary")
    await page.getByRole("tab", { name: "能力词典", exact: true }).click()
    await expect(
      page.getByRole("button", { name: "导出当前词典" }),
    ).toBeEnabled()
    await expect(
      page.getByRole("region", { name: "上传能力词典" }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "在网页上编辑" }),
    ).toHaveCount(0)
    await expectChineseCopy(page)
  })
}

test("web editor saves additions, levels and removals in a new enterprise at 375px", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000)
  await page.addInitScript(() => {
    Object.defineProperty(window.crypto, "randomUUID", { value: undefined })
  })
  const email = `dictionary-editor-${crypto.randomUUID()}@example.com`
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "网页词典合成企业",
      admin_username: email,
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}`
  const definitions = ["retained", "removed"].map((key, index) => ({
    key,
    name: index ? "将删除的能力" : "保留的能力",
    category: "工程",
    description: "合成说明",
    level_rubric: {
      levels: [{ key: "basic", label: "基础", description: "旧等级说明" }],
    },
  }))
  expect(
    (
      await request.put(`${base}/capabilities`, {
        headers,
        data: { definitions, expected_version: 0 },
      })
    ).ok(),
  ).toBeTruthy()
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,工程部,,,synthetic@example.com\n",
        ),
      },
    },
  })
  const imported = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${imported.id}/apply`, {
        headers,
        data: { base_revision: imported.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  const employees = await request.get(`${base}/employees`, { headers })
  const employeeId = (await employees.json()).data[0].id
  await logInUser(page, email, "synthetic-password-2026")
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  const editor = page.getByRole("region", { name: "网页编辑能力词典" })
  const retained = editor.getByRole("article", {
    name: "保留的能力",
    exact: true,
  })
  await expect(retained.getByLabel("能力标识")).toBeDisabled()
  await retained.getByLabel("等级描述").fill("可以独立完成合成任务")
  await editor
    .getByRole("article", { name: "将删除的能力" })
    .getByRole("button", { name: "删除能力" })
    .click()
  await editor.getByRole("button", { name: "新增能力", exact: true }).click()
  const added = editor.getByRole("article").last()
  await added.getByLabel("能力名称").fill("新合成能力")
  await added.getByLabel("能力说明").fill("用于验证未知状态")
  await added.getByLabel("等级描述").fill("能在指导下完成")
  await expect(
    added.getByRole("checkbox", { name: "不由 AI 推断" }),
  ).toHaveCount(0)
  await editor.getByRole("button", { name: "预览改动" }).click()
  const preview = editor.getByRole("region", { name: "词典改动预览" })
  for (const text of [
    "新增 1 项",
    "修改 1 项",
    "删除 1 项",
    "0 条判断保留",
    "0 条待提交改动项将移除",
  ])
    await expect(preview).toContainText(text)
  await expectChineseCopy(page, [
    "YAML",
    "AI",
    "retained",
    "removed",
    "capability",
  ])
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.screenshot({
    path: testInfo.outputPath("dictionary-editor-mobile.png"),
    fullPage: true,
  })
  await preview.getByRole("button", { name: "确认保存新版本" }).click()
  await expect(
    page.getByRole("status").filter({ hasText: "新词典已启用" }),
  ).toBeVisible()
  await page.reload()
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expect(page.getByText("当前第 2 版 · 2 项能力")).toBeVisible()
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  await expect(retained.getByLabel("等级描述")).toHaveValue(
    "可以独立完成合成任务",
  )
  await expect(
    editor.getByRole("article", { name: "将删除的能力" }),
  ).toHaveCount(0)
  await expect(
    editor.getByRole("article", { name: "新合成能力" }),
  ).toBeVisible()
  await page.goto("/admin/drafting")
  await expect(page).toHaveURL("/admin/data-ai?tab=model")
  await expect(
    page.getByRole("switch", { name: "自动起草", exact: true }),
  ).toBeVisible()
  await page.goto(`/employees/${employeeId}`)
  await expect(
    page.getByRole("article", { name: "新合成能力", exact: true }),
  ).toContainText("未知")
})

test("admin browses categories, levels, published counts and searches", async ({
  page,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  const browser = page.getByRole("region", { name: "浏览能力词典" })
  const first = browser.getByRole("article").first()
  await expect(
    first.getByRole("region", { name: "已发布判断统计" }),
  ).toBeVisible()
  await expect(first.getByRole("list", { name: "等级说明" })).toBeVisible()
  await expect(first.getByRole("list", { name: "等级分布" })).toContainText(
    "未定等级",
  )
  await expect(first.getByRole("list", { name: "判断状态分布" })).toContainText(
    "存在争议",
  )
  const name = (await first.getByRole("heading").textContent())?.trim() ?? ""
  expect(name).not.toBe("")
  await browser.getByLabel("搜索能力").fill(name)
  await expect(browser.getByRole("article")).toHaveCount(1)
  const category = browser.locator("summary").first()
  await category.click()
  await expect(first).toBeHidden()
  await category.click()
  await browser.getByLabel("搜索能力").fill("不存在的合成能力")
  await expect(browser.getByRole("article")).toHaveCount(0)
  await expect(browser.getByRole("status")).toContainText("没有匹配的能力")
  await browser.getByLabel("搜索能力").fill("")
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({
    path: "../docs/screenshots/m4.2/capability-browser-admin.png",
    fullPage: true,
  })
})

test("employee browses and searches without requesting statistics", async ({
  page,
}) => {
  const statsRequests: string[] = []
  page.on("request", (request) => {
    if (request.url().includes("/capabilities/stats"))
      statsRequests.push(request.url())
  })
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  const browser = page.getByRole("region", { name: "浏览能力词典" })
  const card = browser.getByRole("article").first()
  await expect(card).toBeVisible()
  const description = (await card.locator("header p").textContent()) ?? ""
  expect(description).not.toBe("")
  await browser.getByLabel("搜索能力").fill(description)
  await expect(browser.getByRole("article")).toHaveCount(1)
  await expect(
    browser.getByRole("region", { name: "已发布判断统计" }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "编辑词典", exact: true }),
  ).toHaveCount(0)
  expect(statsRequests).toEqual([])
})
