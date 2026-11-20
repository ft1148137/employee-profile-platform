import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("较差推荐同时展示规则方案、差别和两个可打开的链接", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(180000)
  const password = "synthetic-coordination-password-2026"
  const api = async (
    url: string,
    data?: object,
    token?: string,
    method = data ? "POST" : "GET",
  ) => {
    const response = await request.fetch(url, {
      method,
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const setup = await api("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await api("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成校验器兜底企业",
    admin_username: `coordination-${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const roster = await request.post(`${base}/roster/imports`, {
    headers: { Authorization: `Bearer ${admin.access_token}` },
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          [
            "工号,姓名,部门,上级工号,邮箱",
            `A,合成需求经理,客户,,a-${crypto.randomUUID()}@example.com`,
            `M,合成答复经理,研发,,m-${crypto.randomUUID()}@example.com`,
            `W,合成研发员工,研发,M,w-${crypto.randomUUID()}@example.com`,
          ].join("\n"),
        ),
      },
    },
  })
  expect(roster.ok(), await roster.text()).toBeTruthy()
  const preview = await roster.json()
  await api(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    admin.access_token,
  )
  await api(
    `${base}/capabilities/versions`,
    {
      expected_version: 0,
      definitions: [
        {
          key: "delivery",
          name: "合成交付",
          category: "工程",
          description: "合成能力",
          level_rubric: {
            levels: [
              { key: "routine", label: "独立完成", description: "能独立完成" },
            ],
          },
        },
      ],
    },
    admin.access_token,
  )
  const issued = await request.post(`${base}/accounts/issue`, {
    headers: { Authorization: `Bearer ${admin.access_token}` },
    data: {},
  })
  expect(issued.ok(), await issued.text()).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await api(`${base}/accounts`, undefined, admin.access_token)
  ).data
  const person = (code: string) =>
    accounts.find((row) => row.employee_code === code)!
  const tokens = new Map<string, string>()
  for (const code of ["A", "M"]) {
    await api(
      `${base}/role-grants`,
      {
        user_id: person(code).account!.user_id,
        role: "dept_manager",
        department_id: person(code).department!.id,
      },
      admin.access_token,
    )
    const reset = await api(
      `${base}/accounts/${person(code).account!.user_id}/reset-password`,
      {},
      admin.access_token,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await api(
      "/api/v1/users/me/password",
      { current_password: reset.initial_password, new_password: password },
      (await login.json()).access_token,
      "PATCH",
    )
    tokens.set(code, changed.access_token)
  }
  await api(
    `${base}/capacity-settings`,
    { enabled: true, requester_policy: "everyone" },
    admin.access_token,
    "PUT",
  )
  const start = (
    await api(`${base}/capacity/workloads/me`, undefined, tokens.get("M"))
  ).weeks[0].week_start as string
  const end = new Date(Date.parse(`${start}T12:00:00Z`) + 13 * 86400000)
    .toISOString()
    .slice(0, 10)
  await api(
    `${base}/employees/${person("W").id}/profile/direct-edits`,
    {
      base_revision: null,
      items: [
        {
          op: "add",
          capability_key: "delivery",
          statement: "合成交付能力",
          level: "routine",
        },
      ],
    },
    admin.access_token,
  )
  await api(
    `${base}/capacity/weeks`,
    {
      items: [{ employee_id: person("W").id, other_percent: 10 }],
      start_week: start,
      end_week: new Date(Date.parse(`${start}T12:00:00Z`) + 7 * 86400000)
        .toISOString()
        .slice(0, 10),
    },
    tokens.get("M"),
    "PUT",
  )
  const demandBody = {
    requesting_department_id: person("A").department!.id,
    target_department_id: person("M").department!.id,
    capability_key: "delivery",
    headcount: 1,
    allocation_percent: 50,
    window_start: start,
    window_end: end,
    description: "合成兜底需求",
  }
  const demand = await api(
    `${base}/capacity/demands`,
    demandBody,
    tokens.get("A"),
  )
  const plan = await api(
    `${base}/planning/demands/${demand.id}/cases`,
    {},
    tokens.get("M"),
  )
  const bad = await api(
    `${base}/planning/cases/${plan.id}/options`,
    {
      assignees: [
        {
          employee_id: person("W").id,
          allocation_percent: 100,
          start_date: start,
          end_date: end,
        },
      ],
    },
    tokens.get("M"),
  )
  expect(bad.verdict).toBe("infeasible")
  const otherDemand = await api(
    `${base}/capacity/demands`,
    { ...demandBody, description: "合成另一条需求" },
    tokens.get("A"),
  )
  const otherPlan = await api(
    `${base}/planning/demands/${otherDemand.id}/cases`,
    {},
    tokens.get("M"),
  )
  const deniedDemand = await api(
    `${base}/capacity/demands`,
    {
      ...demandBody,
      requesting_department_id: person("M").department!.id,
      target_department_id: person("A").department!.id,
    },
    tokens.get("M"),
  )
  const deniedPlan = await api(
    `${base}/planning/demands/${deniedDemand.id}/cases`,
    {},
    admin.access_token,
  )
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const done = body.messages.at(-1).role === "tool"
    const calls = [
      {
        name: "coordination",
        arguments: {
          function: "compare_plan_options",
          arguments: { case_id: plan.id, option_ids: [bad.id] },
        },
      },
      {
        name: "prepare_plan_proposal",
        arguments: {
          case_id: plan.id,
          option_id: bad.id,
          text: "请核对部门答复",
        },
      },
    ]
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({
        id: "fallback",
        object: "chat.completion.chunk",
        choices: [
          {
            index: 0,
            delta: done
              ? { role: "assistant", content: "请核对两个方案" }
              : {
                  role: "assistant",
                  tool_calls: calls.map((call, index) => ({
                    index,
                    id: `fallback-${index}`,
                    type: "function",
                    function: {
                      name: call.name,
                      arguments: JSON.stringify(call.arguments),
                    },
                  })),
                },
            finish_reason: done ? "stop" : "tool_calls",
          },
        ],
      })}\n\ndata: [DONE]\n\n`,
    )
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  const address = server.address()
  if (!address || typeof address === "string")
    throw new Error("Missing model address")
  const host =
    process.env.MODEL_TEST_HOST ??
    (baseURL?.includes("backend:")
      ? Object.values(networkInterfaces())
          .flat()
          .find((network) => network?.family === "IPv4" && !network.internal)
          ?.address
      : "localhost")
  try {
    await api(
      `${base}/model-setting`,
      {
        enabled: true,
        kind: "openai_compatible",
        provider: "openai-compatible",
        model: "synthetic",
        base_url: `http://${host}:${address.port}/v1`,
        api_key: "synthetic-key",
      },
      admin.access_token,
      "PUT",
    )
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token: tokens.get("M")!, org: admin.org_id },
    )
    await page.goto("/assistant")
    const panel = page.getByRole("region", { name: "助手", exact: true })
    await panel.getByLabel("发给助手的消息").fill("帮我核对推荐方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel
      .getByRole("region", { name: "自定方案方案", exact: true })
      .first()
    await expect(card).toContainText("备选方案")
    await expect(card).toContainText("不可行 → 可行")
    await expect(card).toContainText("新人数")
    await expect(card).toContainText("自定方案")
    await expect(
      panel.getByText("请核对两个方案", { exact: true }),
    ).toBeVisible()
    await expect(
      panel.getByRole("region", { name: "答复部门需求", exact: true }),
    ).toHaveCount(0)
    await expectChineseCopy(page)
    const createdSnapshots: string[] = []
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        /\/planning\/demands\/[^/]+\/cases$/.test(req.url())
      )
        createdSnapshots.push(req.url())
    })
    const best = plan.options.find(
      (item: { verdict: string }) => item.verdict === "feasible",
    )
    expect(best).toBeTruthy()
    for (const [name, expectedOption, expectedTitle] of [
      ["在方案页修改", bad.id, "自定方案方案"],
      ["查看备选方案", best.id, "直接安排方案"],
    ] as const) {
      await page.evaluate(
        ({ org, demand }) => {
          localStorage.removeItem(`planning-case:${org}:${demand}`)
        },
        { org: admin.org_id, demand: demand.id },
      )
      await card.getByRole("link", { name, exact: true }).click()
      await expect(page).toHaveURL(
        new RegExp(
          `demand=${demand.id}.*plans=1#case=${plan.id}&option=${expectedOption}$`,
        ),
      )
      await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
      const targetArticle = page.getByRole("article", {
        name: expectedTitle,
        exact: true,
      })
      await expect(targetArticle).toBeVisible()
      await expect(targetArticle).toHaveAttribute("data-selected", "true")
      await expect(targetArticle).toContainText("当前定位方案")
      await expect(
        page.getByRole("article", { name: "直接安排方案", exact: true }),
      ).toBeVisible()
      await expect(
        page.getByRole("article", { name: "自定方案方案", exact: true }),
      ).toBeVisible()
      const current = await api(
        `${base}/planning/cases/${plan.id}`,
        undefined,
        tokens.get("M"),
      )
      expect(current.snapshot_id).toBe(plan.snapshot_id)
      expect(createdSnapshots).toEqual([])
      await page.goto("/assistant")
    }
    await page.goto(
      `/capacity?tab=incoming&demand=${demand.id}&plans=1#case=${otherPlan.id}`,
    )
    await expect(page.getByRole("alert")).toContainText("方案暂时无法读取", {
      timeout: 15000,
    })
    await expect(page.getByRole("article", { name: /方案$/ })).toHaveCount(0)
    expect(createdSnapshots).toEqual([])
    await page.evaluate((id) => {
      window.location.hash = `case=${id}`
    }, plan.id)
    await expect(
      page.getByRole("article", { name: "自定方案方案", exact: true }),
    ).toBeVisible()
    await page.evaluate((id) => {
      window.location.hash = `case=${id}`
    }, deniedPlan.id)
    await expect(page.getByRole("alert")).toContainText(
      "你现在没有权限查看这部分",
      {
        timeout: 15000,
      },
    )
    await expect(page.getByRole("article", { name: /方案$/ })).toHaveCount(0)
    expect(createdSnapshots).toEqual([])
    for (const hash of ["case=invalid", "case=", "invalid"]) {
      await page.evaluate((id) => {
        window.location.hash = `case=${id}`
      }, plan.id)
      await expect(
        page.getByRole("article", { name: "自定方案方案", exact: true }),
      ).toBeVisible()
      await page.evaluate((value) => {
        window.location.hash = value
      }, hash)
      await expect(page.getByRole("alert")).toBeVisible({ timeout: 15000 })
      await expect(page.getByRole("article", { name: /方案$/ })).toHaveCount(0)
      expect(createdSnapshots).toEqual([])
    }
    const unchanged = await api(
      `${base}/capacity/demands/${demand.id}`,
      undefined,
      tokens.get("M"),
    )
    expect(unchanged.revision).toBe(demand.revision)
    for (const hash of [`case=${otherPlan.id}`, "case="]) {
      await page.goto(
        `/capacity?tab=incoming&demand=${demand.id}&plans=1#${hash}`,
      )
      await expect(page.getByRole("alert")).toContainText("方案暂时无法读取", {
        timeout: 15000,
      })
      const before = await api(
        `${base}/planning/cases/${plan.id}`,
        undefined,
        tokens.get("M"),
      )
      const count = createdSnapshots.length
      const simulated = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith(`/planning/demands/${demand.id}/cases`),
      )
      await page.getByRole("button", { name: "重新模拟", exact: true }).click()
      const refreshed = await (await simulated).json()
      expect(refreshed.demand_id).toBe(demand.id)
      expect(refreshed.snapshot_id).not.toBe(before.snapshot_id)
      await expect(page).toHaveURL(
        new RegExp(`demand=${demand.id}.*#case=${refreshed.id}$`),
      )
      await expect(
        page.getByRole("article", { name: "直接安排方案", exact: true }),
      ).toBeVisible()
      await expect(page.getByRole("alert")).toHaveCount(0)
      const displayed = await api(
        `${base}/planning/cases/${refreshed.id}`,
        undefined,
        tokens.get("M"),
      )
      expect(displayed.snapshot_id).toBe(refreshed.snapshot_id)
      expect(createdSnapshots).toHaveLength(count + 1)
    }
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
