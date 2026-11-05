import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin saves approval policy versions and category overrides", async ({
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
  const url = `/api/v1/orgs/${memberships[0].org.id}/approval-policy`
  const original = await (await request.get(url, { headers })).json()
  try {
    await page.goto("/settings")
    await page.getByRole("tab", { name: "审批规则", exact: true }).click()
    const form = page.getByRole("form", { name: "审批规则", exact: true })
    await expect(form.getByLabel("全企业需要经理确认")).toBeVisible()
    await expectChineseCopy(page)
    await form.getByLabel("全企业需要经理确认").uncheck()
    await form.getByLabel(original.categories[0], { exact: true }).click()
    await page.getByRole("option", { name: "需要", exact: true }).click()
    await form.getByLabel("员工过目期限（天）").fill("7")
    await form.getByRole("button", { name: "保存审批规则" }).click()
    await expect(form.getByRole("status")).toHaveText(
      "审批规则已保存为新版本。",
    )
    const saved = await (await request.get(url, { headers })).json()
    expect(saved.current.version).toBe(original.current.version + 1)
    expect(saved.current.require_manager).toBe(false)
    expect(saved.current.category_overrides[original.categories[0]]).toBe(true)
    expect(saved.current.owner_review_days).toBe(7)
    const history = page.getByRole("region", { name: "审批规则历史" })
    await expect(history).toContainText(`第 ${saved.current.version} 版`)
    await expect(history).toContainText("员工过目期限：7 天")
    await form.getByLabel(original.categories[0], { exact: true }).click()
    await page.getByRole("option", { name: "跟随全企业", exact: true }).click()
    await form.getByLabel("员工过目期限（天）").fill("9")
    await form.getByRole("button", { name: "保存审批规则" }).click()
    await expect(history).toContainText(`第 ${saved.current.version + 1} 版`)
    await expect(history).toContainText("员工过目期限：7 天")
    await page.reload()
    await page.getByRole("tab", { name: "审批规则", exact: true }).click()
    await expect(form.getByLabel("员工过目期限（天）")).toHaveValue("9")
    await expect(
      form.getByLabel(original.categories[0], { exact: true }),
    ).toHaveText("跟随全企业")
  } finally {
    expect(
      (
        await request.put(url, {
          headers,
          data: {
            require_manager: original.current.require_manager,
            category_overrides: original.current.category_overrides,
            owner_review_days: original.current.owner_review_days,
          },
        })
      ).ok(),
    ).toBeTruthy()
  }
})

test("employee cannot see approval policy settings", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/settings")
  await expect(page.getByRole("tab", { name: "个人信息" })).toBeVisible()
  await expect(
    page.getByRole("tab", { name: "审批规则", exact: true }),
  ).toHaveCount(0)
})
