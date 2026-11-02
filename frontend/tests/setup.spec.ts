import { expect, type Page, test } from "@playwright/test"
import type { SetupPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })
test.describe.configure({ mode: "serial" })
const invalidLink =
  "初始化链接无效或已过期，请联系部署人员重新运行安装脚本获取新链接"

async function fillSetup(page: Page) {
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试医院")
  await page
    .getByLabel("管理员登录名", { exact: true })
    .fill(`setup-${crypto.randomUUID()}`)
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password-2026")
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-2026")
}

test("initialize, preview and replace dictionary, persist progress and reject reused token at 375px", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const response = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  const { token } = await response.json()
  await page.goto(`/setup#token=${token}`)
  await expect(page).toHaveURL("/setup")
  await expect(page.getByLabel("管理员登录名")).toHaveValue("admin")
  await fillSetup(page)
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("setup-initialize.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page).toHaveURL("/setup-wizard")
  const steps = page.getByRole("navigation", { name: "设置步骤" })
  await steps.getByRole("button", { name: /企业信息/ }).click()
  await page.getByLabel("企业名称", { exact: true }).fill("合成测试医院更新")
  await page.screenshot({
    path: testInfo.outputPath("setup-org-profile.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(
    page.getByRole("heading", { name: "导入员工名单" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByRole("alert")).toContainText("这一步还不能完成")
  await expect(page.getByRole("alert")).toContainText("还没有在职员工")
  await expect(
    page.getByRole("button", { name: "跳过", exact: true }),
  ).toHaveCount(0)
  await steps.getByRole("button", { name: /选择能力词典/ }).click()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByRole("alert")).toContainText("还没有选择能力词典")
  await steps.getByRole("button", { name: /选择能力词典/ }).click()
  await expect(page.getByText("推荐", { exact: true })).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("setup-templates.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: /医院/ }).click()
  const preview = page.getByRole("region", { name: "词典预览" })
  await expect(preview.getByRole("article").first()).toContainText("第 3 级")
  await expectChineseCopy(page, ["PCI", "ICD"])
  await preview.scrollIntoViewIfNeeded()
  await page.screenshot({
    path: testInfo.outputPath("setup-preview.png"),
    fullPage: false,
  })
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText(/当前词典：.*医院/)).toBeVisible()
  await page.getByRole("button", { name: /零售/ }).click()
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText(/当前词典：.*零售/)).toBeVisible()
  await page.getByRole("button", { name: /医院/ }).click()
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText(/当前词典：.*医院/)).toBeVisible()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(
    page.getByRole("heading", { name: "生成员工账号" }),
  ).toBeVisible()
  await page.reload()
  await expect(
    steps.getByRole("button", { name: /选择能力词典/ }),
  ).toContainText("已完成")
  await steps.getByRole("button", { name: /企业信息/ }).click()
  await expect(page.getByLabel("企业名称", { exact: true })).toHaveValue(
    "合成测试医院更新",
  )
  await page.goto("/")
  await expect(page).toHaveURL("/setup-wizard")
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.route("**/orgs/*/setup", async (route) => {
    const data: SetupPublic = await (await route.fetch()).json()
    data.steps.push({
      key: "future",
      title: "未来步骤",
      description: "可选的新增步骤",
      order: 50,
      required: false,
      skippable: true,
      status: "pending",
      blockers: [],
    })
    data.next_step_key = "future"
    await route.fulfill({ json: data })
  })
  await page.route("**/setup/steps/future/skip", async (route) => {
    await route.fulfill({
      json: {
        steps: [],
        completed: true,
        next_step_key: null,
      } satisfies SetupPublic,
    })
  })
  await page.reload()
  await expect(page.getByText("此步骤需要更新系统后才能使用")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "完成此步骤并继续" }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "跳过", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "首次设置已完成" }),
  ).toBeVisible()
  await expectChineseCopy(page)
  await page.goto(`/setup#token=${token}`)
  await fillSetup(page)
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
})

test("missing and invalid initialization links and uninitialized login hint", async ({
  page,
}) => {
  await page.goto("/setup")
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
  await page.goto("/setup#token=invalid")
  await fillSetup(page)
  await page.getByLabel("确认密码", { exact: true }).fill("different-password")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText("两次输入的密码不一致")
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-2026")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText(invalidLink)
  await page.route("**/setup/status", (route) =>
    route.fulfill({ json: { initialized: false } }),
  )
  await page.goto("/login")
  await expect(page.getByRole("status")).toContainText("系统还没有初始化")
  await expectChineseCopy(page)
})
