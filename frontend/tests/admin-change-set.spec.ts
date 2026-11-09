import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const width of [1440, 390]) {
  test(`reminder change is previewed, applied and undone at ${width}px`, async ({
    page,
    request,
    modelUrl,
  }, testInfo) => {
    test.setTimeout(90000)
    const email = `admin-change-${crypto.randomUUID()}@example.com`
    const password = "synthetic-password-2026"
    const token = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: (await token.json()).token,
        org_name: "提醒设置合成企业",
        admin_username: email,
        admin_password: password,
        admin_full_name: "管理员",
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const { org_id: orgId, access_token: accessToken } =
      await initialized.json()
    const base = `/api/v1/orgs/${orgId}`
    const headers = { Authorization: `Bearer ${accessToken}` }
    expect(
      (
        await request.put(`${base}/model-setting`, {
          headers,
          data: {
            enabled: true,
            kind: "openai_compatible",
            provider: "openai-compatible",
            model: "synthetic",
            base_url: modelUrl,
            api_key: "synthetic-key",
          },
        })
      ).ok(),
    ).toBeTruthy()
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(page, email, password)
    await page.setViewportSize({ width, height: 900 })
    await page.goto("/admin/rules?tab=reminders")
    await page.getByRole("button", { name: "打开管理助手" }).click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await dialog.getByRole("button", { name: "发送", exact: true }).click()
    const card = dialog.getByRole("region", { name: "设置改动确认卡" })
    await expect(card).toBeVisible({ timeout: 30000 })
    await expect(card.getByText("每季度", { exact: true })).toBeVisible()
    await expect(card.getByText("每年", { exact: true })).toBeVisible()
    await expect(card.getByText("36 个月", { exact: true })).toBeVisible()
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await card.getByRole("button", { name: "确认应用" }).click()
    await expect(card.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("yearly")
    await page.screenshot({
      path: testInfo.outputPath(`change-applied-${width}.png`),
    })
    await card.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(card.getByRole("status")).toHaveText("已撤销")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await expectChineseCopy(page)
    await page.screenshot({
      path: testInfo.outputPath(`change-undone-${width}.png`),
    })
    await page.reload()
    await page.getByRole("button", { name: "打开管理助手" }).click()
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")
    await page.getByRole("link", { name: "全屏打开", exact: true }).click()
    await expect(page).toHaveURL("/admin/assistant")
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")
  })
}
