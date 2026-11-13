import { expect, type Page, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { randomPassword } from "./utils/random.ts"

test.use({ storageState: { cookies: [], origins: [] } })

const fillForm = async (page: Page, email: string, password: string) => {
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
}

const verifyInput = async (page: Page, testId: string) => {
  const input = page.getByTestId(testId)
  await expect(input).toBeVisible()
  await expect(input).toHaveText("")
  await expect(input).toBeEditable()
}

test("Inputs are visible, empty and editable", async ({ page }) => {
  await page.goto("/login")

  await verifyInput(page, "email-input")
  await verifyInput(page, "password-input")
  await expect(page.getByText("已退出登录", { exact: false })).toHaveCount(0)
})

test("Log In button is visible", async ({ page }) => {
  await page.goto("/login")

  await expect(page.getByRole("button", { name: "登录" })).toBeVisible()
})

test("Password reset hint replaces public account links", async ({ page }) => {
  await page.goto("/login")

  await expect(page.getByText("忘记密码请联系企业管理员重置")).toBeVisible()
  await expect(
    page.locator('a[href="/signup"], a[href="/recover-password"]'),
  ).toHaveCount(0)
  await expect(page.locator("body")).not.toContainText(
    /Login|Log In|Email|Password|Full Stack/,
  )
  await expect(page).toHaveTitle("登录 - 员工画像平台")
})

test("Log in with valid email and password ", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "登录" }).click()

  await page.waitForURL((url) => !url.pathname.startsWith("/login"))

  await expect(page.getByTestId("user-menu")).toBeVisible()
})

test("Log in with email regardless of case", async ({ page }) => {
  await page.goto("/login")
  await fillForm(
    page,
    "EMPLOYEE.PLATFORM@DEMO.EXAMPLE.COM",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  await page.waitForURL(/\/employees\/[0-9a-f-]+$/)
  await expect(page.getByTestId("user-menu")).toBeVisible()
})

test("Log in with invalid email", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, "invalidemail", firstSuperuserPassword)
  await page.getByRole("button", { name: "登录" }).click()

  await expect(page.getByText("邮箱或密码错误")).toBeVisible()
  await expect(page.getByText("登录失败", { exact: true })).toBeVisible()
  await expect(page.locator("body")).not.toContainText("Something went wrong")
})

test("Log in with invalid password", async ({ page }) => {
  const password = randomPassword()

  await page.goto("/login")
  await fillForm(page, firstSuperuser, password)
  await page.getByRole("button", { name: "登录" }).click()

  await expect(page.getByText("邮箱或密码错误")).toBeVisible()
  await expect(page.getByText("登录失败", { exact: true })).toBeVisible()
  await expect(page.locator("body")).not.toContainText("Something went wrong")
})

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 390, height: 844 },
]) {
  test(`Visible logout and one-time notice at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport)
    await page.goto("/login")

    await fillForm(page, firstSuperuser, firstSuperuserPassword)
    await page.getByRole("button", { name: "登录" }).click()

    await page.waitForURL((url) => !url.pathname.startsWith("/login"))

    const logout = page.getByRole("button", { name: "退出登录", exact: true })
    await expect(logout).toBeInViewport({ ratio: 1 })
    await expect(logout).toHaveText("退出登录")
    await logout.click()
    await page.waitForURL("/login")
    await expect(page.getByRole("status")).toHaveText(
      "已退出登录。在公用电脑上用完，请关闭浏览器。",
    )
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBeNull()

    await page.reload()
    await expect(page.getByTestId("email-input")).toBeVisible()
    await expect(page.getByText("已退出登录", { exact: false })).toHaveCount(0)
  })
}

test("Logged-out user cannot access protected routes", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "登录" }).click()

  await page.waitForURL((url) => !url.pathname.startsWith("/login"))

  await expect(page.getByTestId("user-menu")).toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "退出登录" }).click()
  await page.waitForURL("/login")

  await page.goto("/settings")
  await page.waitForURL("/login")
})

for (const path of ["/", "/settings", "/employees"]) {
  test(`Redirects ${path} to /login when token is wrong`, async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("access_token", "invalid_token")
    })
    const documents: string[] = []
    page.on("request", (request) => {
      if (request.resourceType() === "document") {
        documents.push(new URL(request.url()).pathname)
      }
    })
    await page.goto(path)
    await page.waitForURL("/login")
    expect(documents).toEqual([path])
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBeNull()

    await fillForm(page, firstSuperuser, firstSuperuserPassword)
    await page.getByRole("button", { name: "登录" }).click()
    await page.waitForURL(/\/admin\/assistant(?:\?session=[0-9a-f-]+)?$/)
    await expect(page.getByTestId("user-menu")).toBeVisible()
  })
}

test("Layout routes forbidden tokens to login without reloading", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "forbidden_token")
  })
  const documents: string[] = []
  page.on("request", (request) => {
    if (request.resourceType() === "document") {
      documents.push(new URL(request.url()).pathname)
    }
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({ status: 403, json: { detail: "无权访问" } }),
  )
  await page.goto("/settings")
  await page.waitForURL("/login")
  expect(documents).toEqual(["/settings"])
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
})
