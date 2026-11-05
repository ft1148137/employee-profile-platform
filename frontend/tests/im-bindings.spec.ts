import { expect, test } from "@playwright/test"
import type { BindingPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("employees generate a code, see bindings and unbind from personal settings", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  let rows: BindingPublic[] = []
  // Transport and binding are exercised against an HTTP fake in backend tests.
  await page.route("**/me/im-bindings", (route) =>
    route.fulfill({ json: rows }),
  )
  await page.goto("/settings")
  await page.getByRole("tab", { name: "IM 绑定", exact: true }).click()
  await expect(page.getByText("还没有绑定聊天账号")).toBeVisible()
  const generated = page.waitForResponse(
    (response) =>
      response.url().endsWith("/me/im-bind-code") &&
      response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "生成绑定码", exact: true }).click()
  expect((await generated).ok()).toBeTruthy()
  await expect(page.getByTestId("im-bind-code")).toHaveText(/^绑定 [0-9]{6}$/)
  await expect(page.getByText(/10 分钟内有效/)).toBeVisible()
  await page.getByRole("button", { name: "重新生成绑定码" }).click()
  await expect(page.getByTestId("im-bind-code")).toHaveText(/^绑定 [0-9]{6}$/)
  rows = [
    {
      platform: "telegram",
      external_user_id: "100",
      created_at: new Date().toISOString(),
      notify: true,
      active: true,
    },
  ]
  await expect(page.getByText("Telegram · 100")).toBeVisible({ timeout: 15000 })
  let rejectUpdate = false
  await page.route("**/me/im-bindings/telegram", async (route) => {
    expect(route.request().method()).toBe("PATCH")
    const body = route.request().postDataJSON()
    expect(Object.keys(body)).toEqual(["notify"])
    if (rejectUpdate) {
      await route.fulfill({ status: 503, json: { detail: "保存失败，请重试" } })
    } else {
      rows[0].notify = body.notify
      await route.fulfill({ status: 204 })
    }
  })
  const notification = page.getByRole("checkbox", { name: "Telegram 推送通知" })
  await expect(notification).toBeChecked()
  await notification.uncheck()
  await expect(notification).not.toBeChecked()
  await expect(page.getByRole("status")).toHaveText("推送设置已保存")
  await page.reload()
  await page.getByRole("tab", { name: "IM 绑定", exact: true }).click()
  await expect(notification).not.toBeChecked()
  await notification.check()
  await expect(notification).toBeChecked()
  rejectUpdate = true
  await notification.uncheck()
  await expect(page.getByRole("status")).toHaveText("保存失败，请重试")
  await expect(notification).toBeChecked()
  await expectChineseCopy(page, ["Telegram"])
  await page.screenshot({
    path: "test-results/im-bindings.png",
    fullPage: true,
  })
  const removed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/me/im-bindings/telegram") &&
      response.request().method() === "DELETE",
  )
  await page.route("**/me/im-bindings/telegram", async (route) => {
    const response = await route.fetch()
    expect(response.status()).toBe(204)
    rows = []
    await route.fulfill({ response })
  })
  await page.getByRole("button", { name: "解绑 Telegram" }).click()
  expect((await removed).status()).toBe(204)
  await expect(page.getByRole("status")).toContainText("已解绑")
  await expect(page.getByText("还没有绑定聊天账号")).toBeVisible()
  await expect(page.getByTestId("im-bind-code")).toHaveCount(0)
  rows = [
    {
      platform: "telegram",
      external_user_id: "100",
      created_at: new Date().toISOString(),
      notify: true,
      active: false,
    },
  ]
  await expect(
    page.getByText("已失效，请确认入口已开启后重新绑定"),
  ).toBeVisible({ timeout: 15000 })
})

test("accounts without an employee identity cannot generate a binding code", async ({
  page,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/settings")
  await page.getByRole("tab", { name: "IM 绑定", exact: true }).click()
  await expect(page.getByText("当前账号没有员工身份，不能绑定。")).toBeVisible()
  await expect(page.getByRole("button", { name: "生成绑定码" })).toHaveCount(0)
})
