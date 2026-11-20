import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("需求理解卡、经理方案卡和模型失败后的手工答复", async ({
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
    org_name: "合成协调对话企业",
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
  let demandId = ""
  let fail = false
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    if (fail) {
      res.writeHead(503)
      res.end("synthetic failure")
      return
    }
    const body = JSON.parse(raw)
    const done = body.messages.at(-1).role === "tool"
    const latestUser = [...body.messages]
      .reverse()
      .find((item) => item.role === "user")
    const wantsPlan = JSON.stringify(latestUser?.content).includes("模拟")
    const call = wantsPlan
      ? {
          name: "coordination",
          arguments: {
            function: "simulate_plan",
            arguments: { demand_id: demandId },
          },
        }
      : {
          name: "prepare_demand",
          arguments: {
            requesting_department_id: person("A").department!.id,
            target_department_id: person("M").department!.id,
            capability_key: "delivery",
            minimum_level: "routine",
            headcount: 1,
            allocation_percent: 50,
            window_start: start,
            window_end: end,
            description: "合成跨部门交付",
            unclear_fields: ["headcount"],
            questions: ["人数是否确定？"],
          },
        }
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({
        id: "coordination",
        object: "chat.completion.chunk",
        choices: [
          {
            index: 0,
            delta: done
              ? { role: "assistant", content: "请核对卡片" }
              : {
                  role: "assistant",
                  tool_calls: [
                    {
                      index: 0,
                      id: "prepare",
                      type: "function",
                      function: {
                        name: call.name,
                        arguments: JSON.stringify(call.arguments),
                      },
                    },
                  ],
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
  const openAssistant = async (code: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token: tokens.get(code)!, org: admin.org_id },
    )
    await page.goto("/assistant")
    return page.getByRole("region", { name: "助手", exact: true })
  }
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
    let panel = await openAssistant("A")
    await panel.getByLabel("发给助手的消息").fill("请一句话帮我提需求")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const demandCard = panel.getByRole("region", { name: "提交部门需求" })
    await expect(demandCard).toContainText("人数是否确定？")
    await expect(demandCard).toContainText("人数：1待确认")
    await demandCard.getByRole("button", { name: "确认", exact: true }).click()
    await expect(demandCard.last().getByRole("status")).toContainText(
      "需求已提交",
    )
    const demands = await api(
      `${base}/capacity/demands?side=outgoing`,
      undefined,
      tokens.get("A"),
    )
    demandId = demands.data.find(
      (item: { description: string }) => item.description === "合成跨部门交付",
    ).id
    await page.goto(`/capacity?tab=outgoing&demand=${demandId}`)
    await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
      "合成跨部门交付",
    )
    await expectChineseCopy(page)

    panel = await openAssistant("M")
    await panel.getByLabel("发给助手的消息").fill("帮我模拟方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const planCard = panel.getByRole("region", { name: "直接安排方案" })
    await expect(planCard).toContainText("交付日期")
    await expect(planCard).toContainText("审批人")
    const createdCases: string[] = []
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        /\/planning\/demands\/[^/]+\/cases$/.test(req.url())
      )
        createdCases.push(req.url())
    })
    await planCard.getByRole("link", { name: "在方案页修改" }).click()
    await expect(page).toHaveURL(
      new RegExp(`demand=${demandId}.*plans=1#case=.*&option=`),
    )
    await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
    const directArticle = page.getByRole("article", { name: "直接安排方案" })
    await expect(directArticle).toHaveAttribute("data-selected", "true")
    await expect(directArticle).toContainText("当前定位方案")
    expect(createdCases).toEqual([])
    await expectChineseCopy(page)

    panel = await openAssistant("W")
    await panel.getByLabel("发给助手的消息").fill("帮我模拟方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    await expect(panel.getByRole("region", { name: /方案$/ })).toHaveCount(0)
    await expect(
      panel.getByRole("link", { name: "前往待审批", exact: true }),
    ).toHaveCount(0)
    await expectChineseCopy(page)

    fail = true
    panel = await openAssistant("M")
    await panel.getByLabel("发给助手的消息").fill("再帮我模拟方案")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    await panel.getByRole("link", { name: "前往待审批" }).click()
    await expect(page.getByText("合成跨部门交付")).toBeVisible()
    await page.getByRole("link", { name: "查看详情" }).click()
    await page.getByRole("button", { name: "生成方案 / 查看方案" }).click()
    await expect(
      page.getByRole("article", { name: "直接安排方案" }),
    ).toBeVisible()
    const reply = page.getByRole("form", { name: "答复需求" })
    await reply.getByLabel("最早开始日期").fill(start)
    await reply.getByLabel("承诺完成日期").fill(end)
    await reply.getByLabel("答复说明").fill("部门可按约定时间交付")
    await reply.getByRole("button", { name: "确认发送答复" }).click()
    await expect(page.getByText("部门可按约定时间交付")).toBeVisible()
    await expectChineseCopy(page)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
