import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { setupProjectManagers } from "./utils/projects"
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
    await page.goto("/admin/rules?tab=approval")
    await page.getByRole("link", { name: "审批规则", exact: true }).click()
    const form = page.getByRole("form", { name: "审批规则", exact: true })
    await expect(form.getByLabel("默认需要经理审批")).toBeVisible()
    const summary = page.getByRole("region", { name: "当前生效摘要" })
    const [formBox, summaryBox] = await Promise.all([
      form.boundingBox(),
      summary.boundingBox(),
    ])
    expect(summaryBox!.x).toBeGreaterThan(formBox!.x + formBox!.width)
    await expectChineseCopy(page)
    await form.getByLabel("默认需要经理审批").uncheck()
    await form.getByLabel(original.categories[0], { exact: true }).click()
    await page.getByRole("option", { name: "经理审批", exact: true }).click()
    await form.getByLabel("员工确认期限（天）").fill("7")
    await expect(summary).toContainText(
      `${original.current.owner_review_days} 天`,
    )
    await form.getByRole("button", { name: "保存审批规则" }).click()
    await expect(summary).toContainText("7 天")
    await expect(page.getByRole("region", { name: "修改记录" })).toContainText(
      "保存审批规则",
    )
    await expect(form.getByRole("status")).toHaveText("审批规则已保存")
    const saved = await (await request.get(url, { headers })).json()
    expect(saved.current.version).toBe(original.current.version + 1)
    expect(saved.current.require_manager).toBe(false)
    expect(saved.current.category_overrides[original.categories[0]]).toBe(true)
    expect(saved.current.owner_review_days).toBe(7)
    const history = page.getByRole("region", { name: "审批规则历史" })
    await history.getByText("历史版本", { exact: true }).click()
    await expect(history).toContainText(`第 ${saved.current.version} 版`)
    await expect(history).toContainText("员工确认期限：7 天")
    await form.getByLabel(original.categories[0], { exact: true }).click()
    await page.getByRole("option", { name: "跟随默认", exact: true }).click()
    await form.getByLabel("员工确认期限（天）").fill("9")
    await form.getByRole("button", { name: "保存审批规则" }).click()
    await expect(history).toContainText(`第 ${saved.current.version + 1} 版`)
    await expect(history).toContainText("员工确认期限：7 天")
    await page.reload()
    await page.getByRole("link", { name: "审批规则", exact: true }).click()
    await expect(form.getByLabel("员工确认期限（天）")).toHaveValue("9")
    await expect(
      form.getByLabel(original.categories[0], { exact: true }),
    ).toHaveText("跟随默认")
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
  await expect(page.getByRole("region", { name: "个人信息" })).toBeVisible()
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await expect(
    page.getByRole("link", { name: "审批规则", exact: true }),
  ).toHaveCount(0)
})

test("profile preview shows the current approval rule and exempt changes publish as tentative", async ({
  page,
  request,
}) => {
  const { base, adminToken, managerToken, open } = await setupProjectManagers(
    request,
    "质量",
  )
  await open(page, "A", "/assistant")
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.getByRole("button", { name: "添加能力", exact: true }).click()
  const editor = page.getByRole("dialog")
  await editor.getByRole("button", { name: "合成交付", exact: true }).click()
  await editor.getByRole("button", { name: "手动编辑", exact: true }).click()
  const statement = "独立完成合成交付"
  await editor
    .getByRole("textbox", { name: "能力说明", exact: true })
    .fill(statement)
  const preview = editor.getByRole("button", {
    name: "预览并提交",
    exact: true,
  })
  await preview.click()
  await expect(editor.getByText(/^提交后由 .+审批$/)).toBeVisible()
  await editor.getByRole("button", { name: "返回修改", exact: true }).click()

  const policy = await request.put(`${base}/approval-policy`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { category_overrides: { 工程: false } },
  })
  expect(policy.ok(), await policy.text()).toBeTruthy()
  await preview.click()
  await expect(
    editor.getByText("本能力无需经理确认，提交后将直接发布为暂定", {
      exact: true,
    }),
  ).toBeVisible()
  await expect(editor.getByText(/^提交后由 .+审批$/)).toHaveCount(0)
  await editor.getByRole("button", { name: "确认提交", exact: true }).click()

  await expect(editor).toBeHidden()
  const capability = page.getByRole("article", {
    name: "合成交付",
    exact: true,
  })
  await expect(capability.getByText(statement, { exact: true })).toBeVisible()
  await expect(capability.getByText("暂定", { exact: true })).toBeVisible()

  const draft = await request.post(`${base}/me/requests`, {
    headers: { Authorization: `Bearer ${managerToken}` },
    data: {
      items: [
        { op: "retract", capability_key: "delivery", statement: "撤回判断" },
        { op: "add", capability_key: "validation", statement: "补充验证能力" },
      ],
    },
  })
  expect(draft.ok(), await draft.text()).toBeTruthy()
  await page.reload()
  await page.getByRole("button", { name: "继续修改", exact: true }).click()
  await preview.click()

  const retractNotice = "提交后立即撤回，能力状态变为未知"
  const delivery = editor.locator("section").filter({
    has: page.getByRole("heading", { name: "合成交付", exact: true }),
  })
  const validation = editor.locator("section").filter({
    has: page.getByRole("heading", { name: "合成验证", exact: true }),
  })
  await expect(delivery.getByText(retractNotice, { exact: true })).toBeVisible()
  await expect(
    validation.getByText("提交后需经理确认", { exact: true }),
  ).toBeVisible()
  await expect(
    validation.getByText(retractNotice, { exact: true }),
  ).toHaveCount(0)
  await expect(editor.getByText(/直接发布为暂定/)).toHaveCount(0)

  await editor.getByRole("button", { name: "返回修改", exact: true }).click()
  const switched = await request.put(`${base}/approval-policy`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { category_overrides: { 质量: false } },
  })
  expect(switched.ok(), await switched.text()).toBeTruthy()
  await preview.click()
  await expect(
    delivery.getByText("提交后需经理确认", { exact: true }),
  ).toBeVisible()
  await expect(editor.getByText(retractNotice, { exact: true })).toHaveCount(0)
  await expect(validation.getByText(/直接发布为暂定/)).toBeVisible()
})
