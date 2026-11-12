import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi.ts"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser, logOutUser } from "./utils/user"

test("personal information is read-only and account deletion is unavailable", async ({
  page,
}) => {
  await page.goto("/settings")
  await expect(page.getByRole("tab", { name: "个人信息" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
  await expect(page.getByRole("tabpanel")).toContainText("登录名")
  await expect(page.getByRole("tabpanel")).toContainText(firstSuperuser)
  await expect(page.getByRole("tabpanel").locator("input, button")).toHaveCount(
    0,
  )
  await expect(page.getByRole("tab", { name: "删除账号" })).toHaveCount(0)
  await expect(
    page.getByRole("tab", { name: "外观", exact: true }),
  ).toBeVisible()
})

test.describe("Change password", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Update password successfully", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const newPassword = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/settings")
    await page.getByRole("tab", { name: "修改密码" }).click()
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(newPassword)
    await page.getByTestId("confirm-password-input").fill(newPassword)
    await page.getByRole("button", { name: "保存密码" }).click()

    await expect(page.getByText("密码修改成功")).toBeVisible()

    await logOutUser(page)
    await logInUser(page, email, newPassword)
  })
})

test.describe("Change password validation", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  let password: string

  test.beforeAll(async () => {
    email = randomEmail()
    password = randomPassword()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/settings")
    await page.getByRole("tab", { name: "修改密码" }).click()
  })

  test("Update password with weak passwords", async ({ page }) => {
    const weakPassword = "weak"

    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(weakPassword)
    await page.getByTestId("confirm-password-input").fill(weakPassword)
    await page.getByRole("button", { name: "保存密码" }).click()

    await expect(page.getByText("密码至少需要 8 个字符")).toBeVisible()
  })

  test("New password and confirmation password do not match", async ({
    page,
  }) => {
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(randomPassword())
    await page.getByTestId("confirm-password-input").fill(randomPassword())
    await page.getByRole("button", { name: "保存密码" }).click()

    await expect(page.getByText("两次输入的密码不一致")).toBeVisible()
  })

  test("Current password and new password are the same", async ({ page }) => {
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(password)
    await page.getByTestId("confirm-password-input").fill(password)
    await page.getByRole("button", { name: "保存密码" }).click()

    await expect(page.getByText("新密码不能与当前密码相同")).toBeVisible()
  })
})

test("Appearance is available in the user menu", async ({ page }) => {
  await page.goto("/settings")
  await page.getByTestId("user-menu").click()
  await expect(page.getByTestId("theme-button")).toBeVisible()
})

test("User can switch between theme modes", async ({ page }) => {
  await page.goto("/settings")

  await page.getByTestId("user-menu").click()
  await page.getByTestId("theme-button").click()
  await page.getByTestId("dark-mode").click()
  await expect(page.locator("html")).toHaveClass(/dark/)

  await expect(page.getByTestId("dark-mode")).not.toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByTestId("theme-button").click()
  await page.getByTestId("light-mode").click()
  await expect(page.locator("html")).toHaveClass(/light/)
})

test("Selected mode is preserved across sessions", async ({ page }) => {
  await page.goto("/settings")

  await page.getByTestId("user-menu").click()
  await page.getByTestId("theme-button").click()
  await page.getByTestId("light-mode").click()
  await expect(page.locator("html")).toHaveClass(/light/)
  await expect(page.getByTestId("light-mode")).not.toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByTestId("theme-button").click()
  await page.getByTestId("dark-mode").click()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await expect(page.getByTestId("dark-mode")).not.toBeVisible()

  await logOutUser(page)
  await logInUser(page, firstSuperuser, firstSuperuserPassword)
  await expect(page.locator("html")).toHaveClass(/dark/)
})

test.describe("employee settings", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  for (const email of [
    "employee.platform@demo.example.com",
    "manager.platform@demo.example.com",
  ]) {
    test(`${email} reads own fields, linked identities and downloads the skill`, async ({
      page,
    }) => {
      await logInUser(page, email, process.env.DEMO_USER_PASSWORD!)
      await page.goto("/settings")
      await page.getByRole("tab", { name: "档案", exact: true }).click()
      const fields = page.getByRole("region", { name: "员工信息" })
      for (const label of ["部门", "职位", "在职状态", "直属上级"])
        await expect(fields.getByText(label, { exact: true })).toBeVisible()
      await expect(fields.getByText("工号", { exact: true })).toHaveCount(0)
      await expect(fields.locator("input, button")).toHaveCount(0)
      await page.getByRole("tab", { name: "个人信息", exact: true }).click()
      await expect(page.getByRole("tabpanel")).toContainText(email)
      await page.getByRole("tab", { name: "Jira 账号", exact: true }).click()
      const identities = page.getByRole("region", { name: "本人关联账号" })
      await expect(identities).toContainText(
        email.startsWith("employee") ? "demo-jira-02" : "demo-jira-01",
      )
      await expect(
        identities.getByRole("button", { name: /关联|解除/ }),
      ).toHaveCount(0)
      await page.getByRole("tab", { name: "自动起草", exact: true }).click()
      await expect(
        page.getByRole("switch", {
          name: "让系统根据工作记录自动起草我的画像",
        }),
      ).toBeVisible()
      await page.getByRole("tab", { name: "连接 AI 助手", exact: true }).click()
      await expect(
        page.getByText("外部 AI 按指南生成的文件，请在助手对话里上传"),
      ).toBeVisible()
      const downloaded = page.waitForEvent("download")
      await page
        .getByRole("link", { name: "下载生成指南（skill）", exact: true })
        .click()
      expect((await downloaded).suggestedFilename()).toBe("SKILL.md")
    })
  }

  test("account without employee identity has no employee setting tabs", async ({
    page,
  }) => {
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.goto("/settings")
    for (const name of ["档案", "Jira 账号", "自动起草"])
      await expect(page.getByRole("tab", { name, exact: true })).toHaveCount(0)
  })
})
