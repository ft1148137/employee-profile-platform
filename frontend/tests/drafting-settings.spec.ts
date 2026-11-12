import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("drafting settings and personal choice persist with role boundaries", async ({
  page,
  browser,
  request,
}) => {
  const password = process.env.DEMO_USER_PASSWORD!
  await logInUser(page, "admin@demo.example.com", password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const orgId = memberships[0].org.id
  const base = `/api/v1/orgs/${orgId}`
  const original = await (
    await request.get(`${base}/drafting-settings`, { headers })
  ).json()
  const originalNote = await (
    await request.get(`${base}/drafting-note`, { headers })
  ).json()
  const employeePage = await browser.newPage({
    baseURL: new URL(page.url()).origin,
    storageState: { cookies: [], origins: [] },
  })
  const managerPage = await browser.newPage({
    baseURL: new URL(page.url()).origin,
    storageState: { cookies: [], origins: [] },
  })
  await logInUser(employeePage, "employee.platform@demo.example.com", password)
  await expect(employeePage).toHaveURL("/inbox")
  await employeePage
    .getByRole("link", { name: "我的画像", exact: true })
    .click()
  const employeeToken = await employeePage.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  const employeeHeaders = { Authorization: `Bearer ${employeeToken}` }
  const own = await (
    await request.get(`${base}/me/auto-draft`, { headers: employeeHeaders })
  ).json()
  try {
    expect(
      (
        await request.put(`${base}/drafting-settings`, {
          headers,
          data: { enabled: false },
        })
      ).ok(),
    ).toBeTruthy()
    let modelEnabled = false
    await page.route("**/model-setting", async (route) => {
      if (route.request().method() !== "GET") return route.continue()
      const response = await route.fetch()
      await route.fulfill({
        json: { ...(await response.json()), enabled: modelEnabled },
      })
    })
    for (const address of ["/admin/drafting", "/admin/rules?tab=drafting"]) {
      await page.goto(address)
      await expect(page).toHaveURL("/admin/data-ai?tab=model")
      await expect(
        page.getByRole("tab", { name: "自动起草设置", exact: true }),
      ).toHaveCount(0)
    }
    const card = page.getByRole("region", { name: "自动起草", exact: true })
    const note = card.getByLabel("起草补充说明", { exact: true })
    const saveNote = card.getByRole("button", {
      name: "保存补充说明",
      exact: true,
    })
    await note.fill("标签说明工单主题，不单独证明能力。")
    await saveNote.click()
    await expect(card).toContainText("补充说明已保存")
    await page.reload()
    await expect(note).toHaveValue("标签说明工单主题，不单独证明能力。")
    await expect(saveNote).toBeDisabled()
    await expect(card).toContainText("当前版本：")
    await note.fill("字".repeat(2001))
    await expect(saveNote).toBeDisabled()
    await note.fill("")
    await saveNote.click()
    await expect(card).toContainText("补充说明已保存")
    await page.reload()
    await expect(note).toHaveValue("")
    const switchButton = card.getByRole("switch", {
      name: "自动起草",
      exact: true,
    })
    await expect(switchButton).toBeDisabled()
    await expect(card).toContainText("模型未启用")
    modelEnabled = true
    await page.reload()
    await expect(switchButton).toBeEnabled()
    await switchButton.click()
    await expect(switchButton).toBeChecked()
    await page.reload()
    await expect(switchButton).toBeChecked()
    expect(
      (
        await (
          await request.get(`${base}/drafting-settings`, { headers })
        ).json()
      ).enabled,
    ).toBe(true)
    await page.route("**/initial-profiles/draft", (route) =>
      route.fulfill({ status: 202, json: { queued: 1 } }),
    )
    page.once("dialog", (dialog) => dialog.accept())
    await card.getByRole("button", { name: "开始起草", exact: true }).click()
    await expect(card).toContainText("已排队 1 名员工")
    await switchButton.click()
    await expect(switchButton).not.toBeChecked()
    await expect(
      card.getByRole("button", { name: "开始起草", exact: true }),
    ).toBeDisabled()
    await expectChineseCopy(card, ["ChatGPT"])
    await page.screenshot({
      path: "test-results/drafting-settings.png",
      fullPage: true,
    })
    await employeePage.reload()
    await expect(
      employeePage.getByText("企业目前没有打开自动起草。"),
    ).toBeVisible()
    const toggle = employeePage.getByRole("switch", {
      name: "让系统根据工作记录自动起草我的画像",
    })
    if (own.enabled) await toggle.click()
    await expect(toggle).not.toBeChecked()
    await employeePage.reload()
    await expect(toggle).not.toBeChecked()
    await expect(
      employeePage.getByRole("link", { name: "能力与规则", exact: true }),
    ).toHaveCount(0)
    await expectChineseCopy(employeePage)
    await employeePage.screenshot({
      path: "test-results/auto-draft-preference.png",
      fullPage: true,
    })
    await logInUser(managerPage, "manager.platform@demo.example.com", password)
    await managerPage.goto(new URL(employeePage.url()).pathname)
    await expect(
      managerPage.getByRole("heading", { name: "张伟", exact: true }),
    ).toBeVisible()
    await expect(managerPage.getByRole("switch")).toHaveCount(0)
    await expect(
      managerPage.getByRole("link", { name: "能力与规则", exact: true }),
    ).toHaveCount(0)
  } finally {
    expect(
      (
        await request.put(`${base}/drafting-note`, {
          headers,
          data: { text: originalNote.text },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.put(`${base}/drafting-settings`, {
          headers,
          data: { enabled: original.enabled },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.put(`${base}/me/auto-draft`, {
          headers: employeeHeaders,
          data: { enabled: own.enabled },
        })
      ).ok(),
    ).toBeTruthy()
    await employeePage.close()
    await managerPage.close()
  }
})
