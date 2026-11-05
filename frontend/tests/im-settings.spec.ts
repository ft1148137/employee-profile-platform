import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("IM settings require disclosure acknowledgement and never echo credentials", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.waitForURL((url) => url.pathname !== "/")
  await page.getByRole("link", { name: "IM 入口", exact: true }).click()
  await expect(page.getByRole("heading", { name: "IM 入口" })).toBeVisible()
  await expectChineseCopy(page, ["Telegram"])
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const url = `/api/v1/orgs/${memberships[0].org.id}/im-settings`
  const rows = [
    {
      name: "飞书",
      platform: "feishu",
      fields: { 应用标识: "synthetic-app", 应用密钥: "synthetic-app-secret" },
    },
    {
      name: "企业微信",
      platform: "wecom",
      fields: {
        机器人标识: "synthetic-bot",
        机器人密钥: "synthetic-wecom-secret",
      },
    },
    {
      name: "Telegram",
      platform: "telegram",
      fields: { 机器人令牌: "12345:synthetic-token" },
    },
  ]
  try {
    for (const row of rows) {
      const form = page.getByRole("form", { name: row.name, exact: true })
      const enabled = form.getByRole("checkbox", {
        name: "启用入口",
        exact: true,
      })
      if (await enabled.isChecked()) {
        await enabled.uncheck()
        await form.getByRole("button", { name: "保存", exact: true }).click()
        await expect(form.getByRole("status")).toContainText("设置已保存")
      }
      for (const [label, secret] of Object.entries(row.fields))
        await form.getByLabel(label, { exact: true }).fill(secret)
      await enabled.check()
      await expect(
        form.getByText("IM 里的消息会存在该平台的服务器上", { exact: true }),
      ).toBeVisible()
      if (row.platform === "telegram")
        await expect(
          form.getByText("服务器在境外，中国大陆通常连不上"),
        ).toBeVisible()
      const save = form.getByRole("button", { name: "保存", exact: true })
      await expect(save).toBeDisabled()
      await form
        .getByRole("checkbox", { name: "已知晓数据外发提示", exact: true })
        .check()
      await save.click()
      await expect(form.getByRole("status")).toContainText("设置已保存")
      await expect(form.getByText(/凭据已设置/)).toBeVisible()
      for (const label of Object.keys(row.fields))
        await expect(form.getByLabel(label, { exact: true })).toHaveValue("")
      await expect(save).toBeDisabled()
      const response = await request.get(url, { headers })
      const text = await response.text()
      for (const secret of Object.values(row.fields))
        expect(text).not.toContain(secret)
    }
    await page.reload()
    for (const row of rows)
      await expect(
        page
          .getByRole("form", { name: row.name, exact: true })
          .getByRole("checkbox", { name: "启用入口", exact: true }),
      ).toBeChecked()
    await expectChineseCopy(page, ["Telegram"])
    await page.screenshot({
      path: "test-results/im-settings.png",
      fullPage: true,
    })
    await page.route("**/im-settings/telegram/test", (route) =>
      route.fulfill({ json: { ok: false, message: "连接超时，请检查网络" } }),
    )
    const telegram = page.getByRole("form", { name: "Telegram", exact: true })
    await telegram.getByRole("button", { name: "测试已保存的连接" }).click()
    await expect(telegram.getByRole("alert")).toHaveText("连接超时，请检查网络")
    const wecom = page.getByRole("form", { name: "企业微信", exact: true })
    await expect(wecom).toContainText("无法读取企业邮箱时")
    await expect(wecom).toContainText("同一机器人只允许一个服务实例")
    await page.route("**/im-settings/wecom/test", (route) =>
      route.fulfill({
        json: { ok: true, message: "长连接最近心跳时间：合成时间" },
      }),
    )
    await wecom.getByRole("button", { name: "查看长连接最近心跳" }).click()
    await expect(wecom.getByRole("status")).toHaveText(
      "长连接最近心跳时间：合成时间",
    )
    for (const row of rows) {
      const form = page.getByRole("form", { name: row.name, exact: true })
      await form
        .getByRole("checkbox", { name: "启用入口", exact: true })
        .uncheck()
      await form.getByRole("button", { name: "保存", exact: true }).click()
      await expect(form.getByRole("status")).toContainText("设置已保存")
    }
    await page.reload()
    for (const row of rows)
      await expect(
        page
          .getByRole("form", { name: row.name, exact: true })
          .getByRole("checkbox", { name: "启用入口", exact: true }),
      ).not.toBeChecked()
  } finally {
    for (const row of rows)
      expect(
        (
          await request.put(`${url}/${row.platform}`, {
            headers,
            data: { enabled: false },
          })
        ).ok(),
      ).toBeTruthy()
  }
})

test("employees cannot open IM administration", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await expect(
    page.getByRole("link", { name: "IM 入口", exact: true }),
  ).toHaveCount(0)
  await page.goto("/admin/im")
  await expect(page.getByRole("alert")).toContainText("需要企业管理员权限")
})
