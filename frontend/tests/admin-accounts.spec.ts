import { expect, test } from "@playwright/test"
import { departmentOptions } from "../src/components/Admin/departments"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("account filtering, issuance, regeneration guard and atomic conflict", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page).toHaveURL("/setup-wizard")
  await page.getByRole("link", { name: "员工与账号" }).click()
  const rows = page.getByRole("row")
  const row = (code: string) =>
    rows.filter({ has: page.getByRole("cell", { name: code, exact: true }) })
  const filter = async (name: string, option: string) => {
    await page.getByRole("combobox", { name }).click()
    await page.getByRole("option", { name: option, exact: true }).click()
  }
  await expect(row("E03")).toContainText("还没有账号")
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/admin-accounts-list.png",
    fullPage: true,
  })
  await filter("账号状态筛选", "还没有账号")
  await expect(row("E03")).toBeVisible()
  await expect(row("E02")).toHaveCount(0)
  await filter("账号状态筛选", "全部账号状态")
  await page.getByRole("textbox", { name: "搜索姓名或工号" }).fill("E02")
  await expect(page.getByText("共 1 名员工", { exact: false })).toBeVisible()
  await expect(row("E02")).toBeVisible()
  await page.getByRole("textbox", { name: "搜索姓名或工号" }).clear()
  await expect(row("E03")).toBeVisible()
  await filter("部门筛选", "平台研发部")
  await expect(row("E02")).toBeVisible()
  await expect(row("E06")).toHaveCount(0)
  await filter("部门筛选", "全部部门")
  await page.getByRole("button", { name: "生成账号", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(
    dialog.getByText(/将为 \d+ 名还没有账号的在职员工生成账号/),
  ).toBeVisible()
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/admin-accounts-range.png",
    fullPage: true,
  })
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  for (const code of ["E06", "E07"])
    await page
      .getByRole("checkbox", { name: `选择 ${code}`, exact: true })
      .check()
  await page.getByRole("button", { name: "生成账号（已选 2 人）" }).click()
  const regenerate = dialog.getByRole("checkbox")
  if (await regenerate.count()) {
    await expect(
      dialog.getByRole("button", { name: "确认生成并下载" }),
    ).toBeDisabled()
    await regenerate.check()
  }
  const downloadEvent = page.waitForEvent("download")
  await dialog.getByRole("button", { name: "确认生成并下载" }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toMatch(/^账号-.*\.xlsx$/)
  expect(await download.failure()).toBeNull()
  await expect(dialog).toContainText("已生成 2 个账号。")
  await expect(dialog).toContainText("账号文件只能下载这一次")
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/admin-accounts-issued.png",
    fullPage: true,
  })
  await dialog
    .getByRole("button", { name: "关闭", exact: true })
    .first()
    .click()
  for (const code of ["E06", "E07"])
    await expect(row(code)).toContainText("待首次登录")
  await page.getByRole("checkbox", { name: "选择 E06", exact: true }).check()
  await page.getByRole("button", { name: "生成账号（已选 1 人）" }).click()
  await expect(
    dialog.getByRole("button", { name: "确认生成并下载" }),
  ).toBeDisabled()
  await expect(dialog).toContainText("请勾选重新生成")
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  await filter("在职状态筛选", "在职")
  await expect(page.getByText("已选 0 人", { exact: false })).toBeVisible()
  const credentials = { username: "e04", password: "conflict-password-2026" }
  const existing = await request.post("/api/v1/login/access-token", {
    form: credentials,
  })
  if (!existing.ok()) {
    const created = await request.post("/api/v1/private/users/", {
      data: credentials,
    })
    expect(created.ok()).toBeTruthy()
  }
  await page.getByRole("checkbox", { name: "选择 E04", exact: true }).check()
  await page.getByRole("button", { name: "生成账号（已选 1 人）" }).click()
  await dialog.getByRole("button", { name: "确认生成并下载" }).click()
  await expect(dialog.getByRole("alert")).toContainText(
    "有冲突时这一批都没有生成",
  )
  await expect(
    dialog.getByRole("row").filter({ hasText: "登录名已被占用" }),
  ).toContainText("E04")
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/admin-accounts-conflict.png",
    fullPage: true,
  })
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  await expect(row("E04")).toContainText("还没有账号")
})

test("non-admin direct access preserves session and shows the backend reason", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(page.getByRole("link", { name: "员工与账号" })).toHaveCount(0)
  await page.goto("/admin/accounts")
  await expect(page.getByRole("alert")).toContainText("需要企业管理员权限")
  await expect(page).toHaveURL(/\/admin\/accounts$/)
  await expectChineseCopy(page)
})

test("department options include ancestor names", () => {
  const base = { org_id: "org", parent_department_id: null }
  const options = departmentOptions([
    { ...base, id: "root", name: "研发中心" },
    { ...base, id: "child", name: "研发部", parent_department_id: "root" },
  ])
  expect(options.find((d) => d.id === "child")?.name).toBe("研发中心 / 研发部")
})
