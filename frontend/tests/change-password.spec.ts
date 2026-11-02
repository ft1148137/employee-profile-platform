import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("首次登录先改密，保存新令牌后进入首页", async ({ page }, testInfo) => {
  const email = randomEmail()
  const password = randomPassword()
  const newPassword = randomPassword()
  await createUser({
    email,
    password,
    mustChangePassword: true,
    withEmptyProfile: true,
  })
  await page.goto("/login")
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await expect(
    page.getByRole("heading", { name: "请设置新密码" }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("change-password.png"),
    fullPage: true,
  })
  expect(await page.locator("body").textContent()).not.toMatch(/[A-Za-z]{2,}/)
  await page.getByTestId("theme-button").click()
  expect(await page.locator("body").textContent()).not.toMatch(/[A-Za-z]{2,}/)
  await page.keyboard.press("Escape")
  const oldToken = await page.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  await page.goto("/settings")
  await expect(page).toHaveURL("/change-password")
  await page.getByLabel("当前密码", { exact: true }).fill("incorrect-password")
  await page.getByLabel("新密码", { exact: true }).fill(newPassword)
  await page.getByLabel("确认新密码", { exact: true }).fill(newPassword)
  await page.getByRole("button", { name: "保存密码", exact: true }).click()
  await expect(page.getByText("当前密码不正确")).toBeVisible()
  expect(await page.locator("body").textContent()).not.toMatch(/[A-Za-z]{2,}/)
  await page.getByLabel("当前密码", { exact: true }).fill(password)
  await page.getByRole("button", { name: "保存密码", exact: true }).click()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(page.getByText("密码修改成功")).toBeVisible()
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).not.toBe(oldToken)
  await page.reload()
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await page.goto("/change-password")
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
})

test("任意接口要求改密时保留令牌并跳转", async ({ page }) => {
  const email = randomEmail()
  const password = randomPassword()
  await createUser({ email, password })
  await logInUser(page, email, password)
  await page.route("**/api/v1/users/me", async (route) => {
    const response = await route.fetch()
    const data = await response.json()
    await route.fulfill({
      json: {
        ...data,
        must_change_password: page.url().includes("/change-password"),
      },
    })
  })
  await page.route("**/api/v1/me/memberships", (route) =>
    route.fulfill({
      status: 403,
      json: {
        detail: {
          code: "password_change_required",
          message: "首次登录请先修改密码",
        },
      },
    }),
  )
  await page.reload()
  await expect(page).toHaveURL("/change-password")
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeTruthy()
  await page.unrouteAll({ behavior: "wait" })
})

test("布局收到改密错误时通过路由跳转并保留令牌", async ({ page }) => {
  const email = randomEmail()
  const password = randomPassword()
  await createUser({ email, password })
  await logInUser(page, email, password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  let requirePasswordChange = true
  await page.route("**/api/v1/users/me", async (route) => {
    if (requirePasswordChange) {
      requirePasswordChange = false
      await route.fulfill({
        status: 403,
        json: { detail: { code: "password_change_required" } },
      })
      return
    }
    const response = await route.fetch()
    await route.fulfill({
      json: { ...(await response.json()), must_change_password: true },
    })
  })
  const documents: string[] = []
  page.on("request", (request) => {
    if (request.resourceType() === "document") {
      documents.push(new URL(request.url()).pathname)
    }
  })
  await page.goto("/settings")
  await page.waitForURL("/change-password")
  await expect(
    page.getByRole("heading", { name: "请设置新密码" }),
  ).toBeVisible()
  expect(documents).toEqual(["/settings"])
  expect(await page.evaluate(() => localStorage.getItem("access_token"))).toBe(
    token,
  )
  await page.unrouteAll({ behavior: "wait" })
})
