import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("closing manager confirmation requires two clicks with impact", async ({
  page,
  request,
  modelUrl,
}) => {
  const email = `admin-policy-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "二次确认合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
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
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("关闭经理确认")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toBeVisible({ timeout: 30000 })
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  await expect(card.getByRole("alert")).toContainText("影响全体在职员工")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(true)
  await page.reload()
  await expect(
    card.getByRole("button", { name: "再次确认", exact: true }),
  ).toBeVisible()
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(false)
  await expectChineseCopy(page)
})

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

    await page
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const latest = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await latest.getByRole("button", { name: "确认应用" }).click()
    await expect(latest.getByRole("status")).toHaveText("已应用")
    expect(
      (
        await request.put(`${base}/update-settings`, {
          headers,
          data: { reminder_schedule: "off", stale_after_months: 24 },
        })
      ).ok(),
    ).toBeTruthy()
    await latest.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await page.reload()
    const reverse = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await expect(
      reverse.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await expect(reverse.getByRole("status")).toHaveText("待确认")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("off")
    await reverse.getByRole("button", { name: "确认应用" }).click()
    await expect(reverse.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
  })
}
