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
