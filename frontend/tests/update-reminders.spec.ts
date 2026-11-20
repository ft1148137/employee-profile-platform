import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("reminder settings persist and stale badges follow profile data", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const url = `/api/v1/orgs/${memberships[0].org.id}/update-settings`
  const original = await (await request.get(url, { headers })).json()
  const timezoneUrl = `${url}/timezone`
  const originalTimezone = await (
    await request.get(timezoneUrl, { headers })
  ).json()
  try {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto("/admin/rules?tab=reminders")
    const section = page.getByRole("region", { name: "定期更新提醒" })
    await page
      .getByRole("combobox", { name: "企业时区", exact: true })
      .waitFor()
    const summary = page.getByRole("region", { name: "当前生效摘要" })
    await expect(section.getByRole("combobox")).toHaveCount(1)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(375)
    for (const label of ["每年", "关闭", "每季度"]) {
      await section.getByRole("combobox", { name: "提醒周期" }).click()
      await page.getByRole("option", { name: label, exact: true }).click()
      await section.getByLabel("证据过期提醒（月）").fill("36")
      await section.getByRole("button", { name: "保存提醒设置" }).click()
      await expect(section.getByRole("status")).toContainText("已保存")
      await page.reload()
      await expect(
        section.getByRole("combobox", { name: "提醒周期" }),
      ).toHaveText(label)
      await expect(section.getByLabel("证据过期提醒（月）")).toHaveValue("36")
      await expect(summary).toContainText(label)
      await expect(summary).toContainText("36 个月")
    }
    const timezoneSection = page.getByRole("region", { name: "企业时区设置" })
    await timezoneSection
      .getByRole("combobox", { name: "企业时区", exact: true })
      .click()
    await page
      .getByRole("option", { name: "东京时间（Asia/Tokyo）", exact: true })
      .click()
    await timezoneSection.getByRole("button", { name: "保存企业时区" }).click()
    await expect(timezoneSection.getByRole("status")).toContainText("已保存")
    await page.reload()
    await expect(
      timezoneSection.getByRole("combobox", { name: "企业时区", exact: true }),
    ).toHaveText("东京时间")
    await expect(summary).toContainText("东京时间")
    await expect(page.getByRole("region", { name: "修改记录" })).toBeVisible()
    await expectChineseCopy(page)
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.route("**/employees/*/profile", async (route) => {
      const response = await route.fetch()
      const data = await response.json()
      for (const item of data.items)
        item.evidence_stale = item.status === "confirmed"
      await route.fulfill({ response, json: data })
    })
    await page.goto("/employees")
    await page.getByRole("combobox", { name: "部门筛选" }).click()
    await page.getByRole("option", { name: "平台研发部", exact: true }).click()
    await page.getByRole("link", { name: "张伟", exact: true }).click()
    const confirmed = page.getByRole("article", {
      name: "后端 API 开发",
      exact: true,
    })
    await expect(confirmed.getByText("证据较旧", { exact: true })).toBeVisible()
    await expect(confirmed.getByText("已确认", { exact: true })).toBeVisible()
    await expect(
      page
        .getByRole("article")
        .filter({ hasText: "暂无记录（不代表不具备该能力）" })
        .getByText("证据较旧"),
    ).toHaveCount(0)
    await expectChineseCopy(page)
  } finally {
    expect(
      (
        await request.put(timezoneUrl, { headers, data: originalTimezone })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.put(url, {
          headers,
          data: {
            reminder_schedule: original.reminder_schedule,
            stale_after_months: original.stale_after_months,
          },
        })
      ).ok(),
    ).toBeTruthy()
  }
})
