import { expect, test } from "@playwright/test"
import type { DemandPublic } from "../src/client"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] } })

const demandInput = {
  capability_key: "delivery",
  headcount: 1,
  allocation_percent: 30,
  window_start: "2026-12-01",
  window_end: "2026-12-31",
}

test("待审批角标汇总、分类与协作需求的角色入口", async ({ page, request }) => {
  test.setTimeout(90_000)
  const { base, departments, managerToken, open } =
    await setupProjectManagers(request)
  const submitted = await request.post(`${base}/capacity/demands`, {
    headers: { Authorization: `Bearer ${managerToken}` },
    data: {
      ...demandInput,
      requesting_department_id: departments.A,
      target_department_id: departments.B,
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
            submitted_via: "mcp",
            client_name: "合成外部客户端",
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
          borrower_department: "合成借入部门",
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
        count: 1,
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
    rail.getByRole("link", { name: "协同 6", exact: true }),
  ).toBeVisible()
  await expect(
    sidebar.getByRole("link", { name: "待审批 4", exact: true }),
  ).toBeVisible()
  await expect(filters.getByRole("link")).toHaveText([
    "全部4",
    "画像变更1",
    "负荷与延期1",
    "协作需求1",
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
  await expect(
    page.getByText("外部 AI（MCP） · 合成外部客户端", { exact: true }),
  ).toBeVisible()
  await expect(page.getByText("合成调整员工", { exact: false })).toBeVisible()
  await expect(
    page.locator("summary").filter({ hasText: "合成待选人借调" }),
  ).toBeVisible()

  await filters.getByRole("link", { name: /^协作需求/ }).click()
  await expect(page).toHaveURL("/reviews?type=demand")
  await expect(
    filters.getByRole("link", { name: /^协作需求/ }),
  ).toHaveAttribute("aria-current", "page")
  await expect(page.getByRole("region", { name: "画像变更" })).toHaveCount(0)
  const details = page.getByRole("link", { name: "查看详情" })
  const simulate = page.getByRole("link", { name: "用助手拟定方案" })
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
  await expect(filters.getByRole("link", { name: /^协作需求/ })).toHaveText(
    "协作需求0",
  )
  await expect(
    sidebar.getByRole("link", { name: "待审批 3", exact: true }),
  ).toBeVisible()
})

test("待审批按需翻页并打开第 101 条真实需求", async ({ page, request }) => {
  test.setTimeout(90_000)
  const { base, departments, managerToken, open } =
    await setupProjectManagers(request)
  const demands: DemandPublic[] = []
  for (let index = 1; index <= 101; index++) {
    const response = await request.post(`${base}/capacity/demands`, {
      headers: { Authorization: `Bearer ${managerToken}` },
      data: {
        ...demandInput,
        requesting_department_id: departments.A,
        target_department_id: departments.B,
        description: `合成分页需求 ${index}`,
      },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    demands.push(await response.json())
  }
  const detailPath = (id: string) => `/capacity?tab=incoming&demand=${id}`
  const oldest = demands[0]
  const oldestPath = detailPath(oldest.id)
  const expectedFirstPage = demands
    .slice(1)
    .reverse()
    .map(({ id }) => detailPath(id))
  const demandQueries: URLSearchParams[] = []
  page.on("request", (request) => {
    const { pathname, searchParams } = new URL(request.url())
    if (pathname === `${base}/capacity/demands`) {
      demandQueries.push(searchParams)
    }
  })

  const filters = page.getByRole("navigation", { name: "待审批类型" })
  const incoming = page.getByRole("region", { name: "协作需求" })
  const pagination = incoming.getByRole("navigation", { name: "协作需求分页" })
  const details = incoming.getByRole("link", { name: "查看详情" })
  const next = pagination.getByRole("button", { name: "下一页" })
  const previous = pagination.getByRole("button", { name: "上一页" })

  for (const type of ["all", "demand"]) {
    demandQueries.length = 0
    await open(page, "B", type === "all" ? "/reviews" : "/reviews?type=demand")
    await expect(filters.getByRole("link", { name: /^协作需求/ })).toHaveText(
      "协作需求101",
    )
    await expect(
      page.getByRole("navigation", { name: "空间导航" }).getByRole("link", {
        name: "待审批 101",
        exact: true,
      }),
    ).toBeVisible()
    await expect(pagination.getByRole("status")).toHaveText(
      "第 1 页 · 共 101 条",
    )
    await expect(previous).toBeDisabled()
    await expect(details).toHaveCount(100)
    expect(
      await details.evaluateAll((links) =>
        links.map((link) => link.getAttribute("href")),
      ),
    ).toEqual(expectedFirstPage)
    expect(demandQueries.map((query) => query.get("skip"))).toEqual([null])

    await next.click()
    await expect(pagination.getByRole("status")).toHaveText(
      "第 2 页 · 共 101 条",
    )
    await expect(next).toBeDisabled()
    await expect(details).toHaveCount(1)
    await expect(details).toHaveAttribute("href", oldestPath)
    await expect(
      incoming.getByText(oldest.description, { exact: true }),
    ).toBeVisible()
    expect(demandQueries.map((query) => query.get("skip"))).toEqual([
      null,
      "100",
    ])
    for (const query of demandQueries) {
      expect(query.get("side")).toBe("incoming")
      expect(query.get("status")).toBe("submitted")
      expect(query.get("actionable_only")).toBe("true")
      expect(query.get("limit")).toBe("100")
    }
    if (type === "all") {
      await previous.click()
      await expect(details).toHaveCount(100)
      expect(demandQueries).toHaveLength(2)
    }
  }

  await details.click()
  await expect(page).toHaveURL(oldestPath)
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    oldest.description,
  )
})
