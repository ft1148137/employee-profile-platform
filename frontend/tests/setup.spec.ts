import { execFileSync } from "node:child_process"
import path from "node:path"
import { expect, type Page, test } from "@playwright/test"
import type { SetupPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { logOutUser } from "./utils/user"

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

test("complete setup with 30 employees, download accounts and first employee login", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000)
  const response = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  await page.goto(`/setup#token=${(await response.json()).token}`)
  await fillSetup(page)
  await page.getByLabel("姓名（可选）").fill("测试管理员")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page).toHaveURL("/setup-wizard")
  await page
    .getByLabel("拖拽名单到这里，或选择文件上传")
    .setInputFiles(
      path.resolve(import.meta.dirname, "../../examples/roster/sample-30.csv"),
    )
  const preview = page.getByRole("region", { name: "名单预览" })
  await expect(preview.getByText("新增 30 人", { exact: true })).toBeVisible()
  await expect(preview.getByText("导入后的部门树")).toBeVisible()
  await expect(preview.getByText(/研发中心 ·/)).toBeVisible()
  await expectChineseCopy(page, ["sample"])
  await page.screenshot({
    path: testInfo.outputPath("roster-preview.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await page.getByRole("button", { name: /科技研发/ }).click()
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText("词典已启用，可以完成此步骤。")).toBeVisible()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByText("还有 30 名在职员工没有账号")).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("setup-accounts.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "生成账号", exact: true }).click()
  const downloadEvent = page.waitForEvent("download")
  await page.getByRole("button", { name: "确认生成并下载" }).click()
  const download = await downloadEvent
  const xml = execFileSync(
    "unzip",
    ["-p", (await download.path())!, "xl/worksheets/sheet1.xml"],
    { encoding: "utf8" },
  )
  const rows = await page.evaluate(
    (content) =>
      Array.from(
        new DOMParser()
          .parseFromString(content, "application/xml")
          .querySelectorAll("row"),
        (row) =>
          Array.from(
            row.querySelectorAll("c"),
            (cell) => cell.textContent ?? "",
          ),
      ),
    xml,
  )
  expect(rows).toHaveLength(31)
  expect(rows[0]).toEqual([
    "工号",
    "姓名",
    "部门",
    "登录地址",
    "初始密码",
    "备注",
  ])
  expect(new Set(rows.slice(1).map((row) => row[0])).size).toBe(30)
  const employee = rows.find((row) => row[0] === "XY002")!
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  await expect(page.getByText("还有 0 名在职员工没有账号")).toBeVisible()
  const managers = page.getByRole("combobox", {
    name: "星衍科技 / 管理中心 / 总经办经理",
    exact: true,
  })
  await managers.selectOption({ label: "欧阳宁（XY001）" })
  await managers.locator("..").getByRole("button", { name: "保存经理" }).click()
  await expect(page.getByText("部门经理已保存。")).toBeVisible()
  await expect(managers.locator("..")).toContainText("当前经理：欧阳宁")
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await page.getByRole("button", { name: "生成初始画像", exact: true }).click()
  await expect(
    page.getByText(
      "已为 30 名员工生成初始画像，员工登录后能在自己的画像页看到",
    ),
  ).toBeVisible({ timeout: 60000 })
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible()
  await expect(
    page.getByRole("link", { name: "员工列表", exact: true }),
  ).toBeVisible()
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("setup-completed.png"),
    fullPage: true,
  })
  await logOutUser(page)
  await page.getByTestId("email-input").fill(employee[0])
  await page.getByTestId("password-input").fill(employee[4])
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await page.getByLabel("当前密码", { exact: true }).fill(employee[4])
  await page
    .getByLabel("新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page
    .getByLabel("确认新密码", { exact: true })
    .fill("synthetic-new-password-2026")
  await page.getByRole("button", { name: "保存密码" }).click()
  await expect(page).toHaveURL(/\/employees\//)
  await expect(page.getByText("你的画像还没有内容")).toBeVisible()
})

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
  const username = page.getByLabel("管理员登录名", { exact: true })
  const validUsername = await username.inputValue()
  await username.fill("it@example.com")
  await page.getByRole("button", { name: "创建企业并继续" }).click()
  await expect(page.getByRole("alert")).toHaveText(
    "管理员登录名不能包含 @，请用字母或数字，例如 admin",
  )
  await username.fill(validUsername)
  await expectChineseCopy(page, ["admin"])
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
  await expect(page.getByRole("heading", { name: "设置完成" })).toBeVisible()
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
