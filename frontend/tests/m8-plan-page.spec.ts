import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { setupPlans } from "./utils/plans"

test.use({ storageState: { cookies: [], origins: [] } })

test("B 方经理无需模型查看方案，A 方经理看不到入口", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  const { api, base, person, a, m, start, later, demand, open } =
    await setupPlans(page, request)
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/planning/demands/${demand.id}/cases`) &&
      response.request().method() === "POST",
  )
  await open(m)
  await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
  const created = await (await createdResponse).json()
  const card = page.getByRole("article", { name: "直接安排方案" })
  await expect(card).toBeVisible()
  await expect(
    page.getByRole("article", { name: "优化方案", exact: true }),
  ).toBeVisible()
  const current = await api(
    `${base}/planning/cases/${created.id}`,
    undefined,
    m,
  )
  const direct = current.options.find(
    (option: { kind: string }) => option.kind === "direct",
  )
  expect(direct).toBeTruthy()
  await expect(card).toContainText(direct.kind_label)
  await expect(card).toContainText(direct.verdict_label)
  await expect(card).toContainText(`交付日期：${direct.summary.finish_date}`)
  await expect(card).toContainText(
    `覆盖 ${direct.summary.covered_headcount} 人`,
  )
  await expect(card).toContainText(
    `还差 ${direct.summary.gap_person_weeks} 人周`,
  )
  await expect(card).toContainText("合成研发甲")
  await expect(card).toContainText("合成研发乙")
  await expect(card).toContainText("已投入 20%")
  await expect(card).toContainText("风险")
  expect(direct.findings.length).toBeGreaterThan(0)
  expect(direct.summary.affected_assignment_ids.length).toBeGreaterThan(0)
  await expect(card).toContainText(direct.findings[0].message)
  await expect(card).toContainText(start)
  await expect(card).toContainText(later(13))
  await expect(card).toContainText("本部门经理确认答复")
  await expectChineseCopy(page)
  expect(await page.locator("body").textContent()).not.toMatch(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )
  await page.getByRole("button", { name: "返回待审批" }).click()
  await expect
    .poll(() => new URL(page.url()).searchParams.get("demand"))
    .toBeNull()
  await page
    .getByRole("article")
    .filter({ hasText: "合成方案需求" })
    .getByRole("link", { name: "查看详情" })
    .click()
  await expect
    .poll(() => new URL(page.url()).searchParams.get("demand"))
    .toBe(demand.id)
  await page.getByRole("button", { name: "生成方案 / 查看方案" }).click()
  await expect
    .poll(() => new URL(page.url()).searchParams.get("plans"))
    .toBe("1")
  await expect(card).toBeVisible()
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("B1").id, other_percent: 30 }],
      start_week: start,
      end_week: later(14),
    },
    m,
    "PUT",
  )
  await page.getByRole("button", { name: "关闭方案" }).click()
  await page.getByRole("button", { name: "生成方案 / 查看方案" }).click()
  await expect(page.getByText("数据已变化，请重新模拟")).toBeVisible()
  await page.getByRole("button", { name: "重新模拟" }).click()
  await expect(page.getByText("数据已变化，请重新模拟")).toHaveCount(0)
  const caseUrl = `**/planning/cases/${created.id}`
  const conflict = "部分候选人已不在当前管辖范围内，请重新模拟"
  await page.route(caseUrl, async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: conflict }),
    })
  })
  await page.reload()
  await expect(page.getByRole("alert")).toContainText(conflict, {
    timeout: 15000,
  })
  await page.unroute(caseUrl)
  await page.getByRole("button", { name: "重新模拟" }).click()
  await expect(card).toBeVisible()
  await open(a)
  await expect(
    page.getByRole("button", { name: /生成方案|查看方案/ }),
  ).toHaveCount(0)
  await expect(page.locator("body")).not.toContainText("合成研发甲")

  const unconfirmed = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 20,
      window_start: later(21),
      window_end: later(27),
      description: "合成未确认周方案需求",
    },
    a,
  )
  await page.goto("/login")
  await page.evaluate(
    ({ token, org }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", org)
    },
    { token: m, org: base.split("/").pop()! },
  )
  await page.goto(`/capacity?tab=incoming&demand=${unconfirmed.id}&plans=1`)
  const insufficientCard = page.getByRole("article", {
    name: "直接安排方案",
    exact: true,
  })
  await expect(insufficientCard).toContainText("数据不足")
  await expect(insufficientCard).toContainText("需求与容量 → 团队负荷")
  await expect(insufficientCard).toContainText("有管辖权的经理")
  await insufficientCard
    .getByRole("link", { name: "去确认团队负荷", exact: true })
    .click()
  await expect(page).toHaveURL(/\/capacity\?tab=team/)
  await expect(
    page.getByRole("table", { name: "团队负荷，按部门、姓名排序" }),
  ).toBeVisible()
})
