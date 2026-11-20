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
  await expect(
    page.getByRole("heading", { name: "管理助手", exact: true }),
  ).toBeVisible()
  await page.getByRole("link", { name: "设置", exact: true }).click()
  await page.getByRole("link", { name: "IM 入口", exact: true }).click()
  await expect(page.getByRole("heading", { name: "IM 入口" })).toBeVisible()
  await expectChineseCopy(page, ["Telegram"])
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
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
        应用标识: "synthetic-bot",
        应用密钥: "synthetic-wecom-secret",
      },
    },
    {
      name: "Telegram",
      platform: "telegram",
      fields: { 应用令牌: "12345:synthetic-token" },
    },
  ]
  try {
    for (const row of rows) {
      await page
        .getByRole("button", { name: `配置 ${row.name}`, exact: true })
        .click()
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
          form.getByText("服务器位于境外，境内网络可能无法直连。"),
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
      for (const secret of Object.values(row.fields)) {
        expect(text).not.toContain(secret)
        expect(await page.content()).not.toContain(secret)
        expect(page.url()).not.toContain(secret)
      }
      await page.getByRole("button", { name: "关闭", exact: true }).click()
    }
    await page.reload()
    for (const row of rows) {
      const card = page.getByRole("region", { name: row.name, exact: true })
      await expect(card).toContainText("已启用")
      await expect(card).toContainText("凭据已设置")
    }
    await expectChineseCopy(page, ["Telegram"])
    await page.screenshot({
      path: "test-results/im-settings.png",
      fullPage: true,
    })
    await page.route("**/im-settings/telegram/test", (route) =>
      route.fulfill({ json: { ok: false, message: "连接超时，请检查网络" } }),
    )
    await page
      .getByRole("button", { name: "配置 Telegram", exact: true })
      .click()
    const telegram = page.getByRole("form", { name: "Telegram", exact: true })
    await telegram.getByRole("button", { name: "测试已保存的连接" }).click()
    await expect(telegram.getByRole("alert")).toHaveText("连接超时，请检查网络")
    await page.getByRole("button", { name: "关闭", exact: true }).click()
    await page
      .getByRole("button", { name: "配置 企业微信", exact: true })
      .click()
    const wecom = page.getByRole("form", { name: "企业微信", exact: true })
    await expect(wecom).toContainText("无法读取企业邮箱时")
    await expect(wecom).toContainText("同一应用只允许一个服务实例")
    await page.route("**/im-settings/wecom/test", (route) =>
      route.fulfill({
        json: { ok: true, message: "长连接最近心跳时间：合成时间" },
      }),
    )
    await wecom.getByRole("button", { name: "查看长连接最近心跳" }).click()
    await expect(wecom.getByRole("status")).toHaveText(
      "长连接最近心跳时间：合成时间",
    )
    await page.getByRole("button", { name: "关闭", exact: true }).click()
    await page.route("**/im-settings/feishu/test", (route) =>
      route.fulfill({ json: { ok: true, message: "连接成功" } }),
    )
    await page.getByRole("button", { name: "配置 飞书", exact: true }).click()
    const feishu = page.getByRole("form", { name: "飞书", exact: true })
    await feishu.getByRole("button", { name: "测试已保存的连接" }).click()
    await expect(feishu.getByRole("status")).toContainText("连接成功")
    await page.getByRole("button", { name: "关闭", exact: true }).click()
    for (const row of rows) {
      await page
        .getByRole("button", { name: `配置 ${row.name}`, exact: true })
        .click()
      const form = page.getByRole("form", { name: row.name, exact: true })
      await form
        .getByRole("checkbox", { name: "启用入口", exact: true })
        .uncheck()
      await form.getByRole("button", { name: "保存", exact: true }).click()
      await expect(form.getByRole("status")).toContainText("设置已保存")
      await page.getByRole("button", { name: "关闭", exact: true }).click()
    }
    await page.reload()
    for (const row of rows)
      await expect(
        page.getByRole("region", { name: row.name, exact: true }),
      ).toContainText("未启用")
    await page.setViewportSize({ width: 390, height: 844 })
    const configure = page.getByRole("button", {
      name: "配置 飞书",
      exact: true,
    })
    await configure.click()
    const drawer = page.getByRole("dialog", { name: "配置 · 飞书" })
    await expect(drawer).toBeVisible()
    expect(
      await drawer.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBeTruthy()
    await page.keyboard.press("Escape")
    await expect(drawer).toHaveCount(0)
    await expect(configure).toBeFocused()
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

for (const [tab, message] of [
  ["sources", "需要企业管理员权限"],
  ["model", "需要企业管理员权限"],
  ["bot", "需要企业管理员权限"],
  ["im", "需要企业管理员权限"],
  ["mcp", "需要企业管理员权限"],
]) {
  test(`employees cannot open data and AI administration: ${tab}`, async ({
    page,
  }) => {
    await logInUser(
      page,
      "employee.platform@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.getByRole("link", { name: "设置", exact: true }).click()
    await expect(
      page.getByRole("link", { name: "数据来源", exact: true }),
    ).toHaveCount(0)
    await page.goto(`/admin/data-ai?tab=${tab}`)
    await expect(page.getByRole("alert")).toHaveText(message)
    await expect(page.getByRole("tablist")).toHaveCount(0)
  })
}
