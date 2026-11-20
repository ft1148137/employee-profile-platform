import { expect, test } from "@playwright/test"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] } })

test("待审批角标汇总、分类与收到需求的角色入口", async ({ page, request }) => {
  test.setTimeout(90_000)
  const { base, departments, managerToken, open } =
    await setupProjectManagers(request)
  const submitted = await request.post(`${base}/capacity/demands`, {
    headers: { Authorization: `Bearer ${managerToken}` },
    data: {
      requesting_department_id: departments.A,
      target_department_id: departments.B,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 30,
      window_start: "2026-12-01",
      window_end: "2026-12-31",
      description: "合成待答复需求",
    },
  })
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const demand = await submitted.json()

  await page.route(`**${base}/reviews`, (route) =>
    route.fulfill({
      json: {
        count: 1,
        data: [
          {
            id: crypto.randomUUID(),
            employee_name: "合成画像员工",
            item_count: 2,
            owner_reviewed: true,
            can_decide: true,
          },
        ],
      },
    }),
  )
  await page.route(`**${base}/capacity/workload-requests`, (route) =>
    route.fulfill({
      json: {
        count: 2,
        data: [
          {
            id: "pending",
            status: "pending",
            employee_name: "合成调整员工",
            requested_percent: 50,
          },
          { id: "done", status: "approved" },
        ],
      },
    }),
  )
  await page.route(`**${base}/capacity/borrow-requests?mine=false`, (route) =>
    route.fulfill({
      json: [
        {
          id: "pending",
          status: "pending_selection",
          description: "合成待选人借调",
          capability_name: "合成交付",
        },
        { id: "done", status: "selected" },
      ],
    }),
  )
  await page.route(`**${base}/me/inbox?*`, (route) =>
    route.fulfill({
      json: { data: [], count: 0, unread_count: 2, pending_count: 0 },
    }),
  )
  const demandList = `**${base}/capacity/demands?*`
  const demandQueries: URLSearchParams[] = []
  await page.route(demandList, (route) => {
    demandQueries.push(new URL(route.request().url()).searchParams)
    return route.fulfill({
      json: {
        count: 101,
        data: [
          { ...demand, can_reply: true },
          {
            ...demand,
            id: crypto.randomUUID(),
            capability_name: "合成不可答复需求",
            can_reply: false,
          },
        ],
      },
    })
  })

  await open(page, "B", "/reviews")
  const rail = page.getByRole("navigation", { name: "主导航" })
  const sidebar = page.getByRole("navigation", { name: "空间导航" })
  const filters = page.getByRole("navigation", { name: "待审批类型" })
  await expect(
    rail.getByRole("link", { name: "协同 106", exact: true }),
  ).toBeVisible()
  await expect(
    sidebar.getByRole("link", { name: "待审批 104", exact: true }),
  ).toBeVisible()
  await expect(filters.getByRole("link")).toHaveText([
    "全部104",
    "画像变更1",
    "负荷与延期1",
    "收到需求101",
    "借调确认1",
  ])
  expect(demandQueries).toHaveLength(1)
  const [query] = demandQueries
  expect(query.get("side")).toBe("incoming")
  expect(query.get("status")).toBe("submitted")
  expect(query.get("actionable_only")).toBe("true")
  expect(query.get("limit")).toBe("100")
  expect(query.has("skip")).toBe(false)
  await expect(page.getByText("合成不可答复需求")).toHaveCount(0)
  await expect(page.getByText("合成画像员工", { exact: false })).toBeVisible()
  await expect(page.getByText("合成调整员工", { exact: false })).toBeVisible()
  await expect(page.getByText("合成待选人借调")).toBeVisible()

  await filters.getByRole("link", { name: /^收到需求/ }).click()
  await expect(page).toHaveURL("/reviews?type=demand")
  await expect(
    filters.getByRole("link", { name: /^收到需求/ }),
  ).toHaveAttribute("aria-current", "page")
  await expect(page.getByRole("region", { name: "画像变更" })).toHaveCount(0)
  const details = page.getByRole("link", { name: "查看详情" })
  const simulate = page.getByRole("link", { name: "在 AI 助手中模拟并答复" })
  const demandPath = `/capacity?tab=incoming&demand=${demand.id}`
  await expect(details).toHaveAttribute("href", demandPath)
  await simulate.click()
  await expect(page).toHaveURL(/\/assistant\?mode=personal&prompt=/)
  const prompt = new URL(page.url()).searchParams.get("prompt")
  expect(prompt).toContain(demand.id)
  expect(prompt).toContain("合成交付")

  await page.unroute(demandList)
  await open(page, "admin", "/reviews?type=demand")
  await expect(details).toHaveAttribute("href", demandPath)
  await expect(simulate).toHaveCount(0)
  await details.click()
  await expect(page).toHaveURL(demandPath)
  const reply = page.getByRole("form", { name: "答复需求" })
  await reply.getByLabel("答复结论").selectOption("can_do")
  await reply.getByLabel("最早开始日期").fill("2026-12-01")
  await reply.getByLabel("承诺完成日期").fill("2026-12-25")
  await reply.getByLabel("答复说明").fill("合成确认答复")
  await reply.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "已答复",
  )
  await page.getByRole("button", { name: "返回待审批" }).click()
  await expect(filters.getByRole("link", { name: /^收到需求/ })).toHaveText(
    "收到需求0",
  )
  await expect(
    sidebar.getByRole("link", { name: "待审批 3", exact: true }),
  ).toBeVisible()
})
