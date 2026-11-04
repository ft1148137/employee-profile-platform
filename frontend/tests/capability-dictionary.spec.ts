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
  await page.goto("/settings")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expectChineseCopy(page, ["YAML", "AI"])
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "下载模板（Excel）" }).click()
  expect((await download).suggestedFilename()).toBe("能力词典模板.xlsx")
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

test("employee can export but cannot upload", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/settings")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expect(page.getByRole("button", { name: "导出当前词典" })).toBeEnabled()
  await expect(page.getByRole("region", { name: "上传能力词典" })).toHaveCount(
    0,
  )
  await expectChineseCopy(page)
})
