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
  for (const code of ["A", "M", "W"]) {
    if (code !== "W") {
      await api(
        `${base}/role-grants`,
        {
          user_id: person(code).account!.user_id,
          role: "dept_manager",
          department_id: person(code).department!.id,
        },
        admin.access_token,
      )
    }
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
  const demand = await api(
    `${base}/capacity/demands`,
    {
      requesting_department_id: person("A").department!.id,
      target_department_id: person("M").department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 50,
      window_start: start,
      window_end: end,
      description: "合成兜底需求",
    },
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
    await panel.getByLabel("发给机器人的消息").fill("帮我核对推荐方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel
      .getByRole("region", { name: "自定方案方案", exact: true })
      .first()
    await expect(card).toContainText("规则引擎方案")
    await expect(card).toContainText("不可行 → 可行")
    await expect(card).toContainText("新人数")
    const reply = panel.getByRole("region", {
      name: "答复部门需求",
      exact: true,
    })
    await expect(reply).toContainText("规则引擎方案")
    await expect(reply).toContainText("不可行 → 可行")
    await expect(
      reply.getByRole("button", { name: "确认", exact: true }),
    ).toBeEnabled()
    await expectChineseCopy(page)
    for (const name of ["在方案页修改", "查看规则引擎方案"]) {
      await card.getByRole("link", { name, exact: true }).click()
      await expect(page).toHaveURL(new RegExp(`demand=${demand.id}.*plans=1`))
      await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
      await expect(
        page.getByRole("article", { name: "直接安排方案", exact: true }),
      ).toBeVisible()
      await expect(
        page.getByRole("article", { name: "自定方案方案", exact: true }),
      ).toBeVisible()
      await page.goto("/assistant")
    }
    const unchanged = await api(
      `${base}/capacity/demands/${demand.id}`,
      undefined,
      tokens.get("M"),
    )
    expect(unchanged.revision).toBe(demand.revision)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
