import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("manager confirms a review from the assistant", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-review-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成审批企业",
      admin_username: `review-${crypto.randomUUID()}@example.com`,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,研发部,经理,,manager-${crypto.randomUUID()}@example.com\nE,合成员工,研发部,工程师,M,employee-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  const preview = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await request.post(`${base}/capabilities/versions`, {
        headers,
        data: {
          expected_version: 0,
          definitions: [1].map((number) => ({
            key: `skill_${number}`,
            name: `合成能力${number}`,
            category: "合成能力",
            description: "审批测试",
            level_rubric: {
              levels: [
                {
                  key: "routine",
                  label: "独立完成",
                  description: "能独立完成",
                },
              ],
            },
          })),
        },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const subject = accounts.find((value) => value.employee_code === "E")!
  const manager = accounts.find((value) => value.employee_code === "M")!
  expect(
    (
      await request.post(`${base}/role-grants`, {
        headers,
        data: {
          user_id: manager.account!.user_id,
          role: "dept_manager",
          department_id: manager.department!.id,
        },
      })
    ).ok(),
  ).toBeTruthy()
  const login = async (account: EmployeeAccount) => {
    const reset = await (
      await request.post(
        `${base}/accounts/${account.account!.user_id}/reset-password`,
        { headers },
      )
    ).json()
    const result = await (
      await request.post("/api/v1/login/access-token", {
        form: { username: reset.username, password: reset.initial_password },
      })
    ).json()
    return (
      await (
        await request.patch("/api/v1/users/me/password", {
          headers: { Authorization: `Bearer ${result.access_token}` },
          data: {
            current_password: reset.initial_password,
            new_password: password,
          },
        })
      ).json()
    ).access_token
  }

  const ownerToken = await login(subject)
  const managerToken = await login(manager)
  const ownerHeaders = { Authorization: `Bearer ${ownerToken}` }
  const draftResponse = await request.post(`${base}/me/requests`, {
    headers: ownerHeaders,
    data: {
      items: [
        {
          op: "add",
          capability_key: "skill_1",
          statement: "独立完成接口联调",
          level: "routine",
          owner_statement: "隐藏的受限自述",
        },
      ],
    },
  })
  expect(draftResponse.ok()).toBeTruthy()
  const draft = await draftResponse.json()
  expect(
    (
      await request.post(`${base}/me/requests/${draft.id}/submit`, {
        headers: ownerHeaders,
        data: { revision: draft.revision },
      })
    ).ok(),
  ).toBeTruthy()
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    expect(raw).not.toContain("隐藏的受限自述")
    const last = body.messages.at(-1)
    const toolName = last.role === "tool" ? last.tool_call_id : null
    const result = toolName ? JSON.parse(last.content) : null
    const name = !toolName
      ? "list_pending_reviews"
      : toolName === "list_pending_reviews"
        ? "get_review"
        : "prepare_review_decision"
    const args =
      name === "list_pending_reviews"
        ? {}
        : name === "get_review"
          ? { request_id: result.data[0].id }
          : {
              request_id: result.id,
              revision: result.revision,
              decisions: result.items.map(
                (item: { after: { id: string } }) => ({
                  item_id: item.after.id,
                  decision: "approved",
                }),
              ),
            }
    const done = toolName === "prepare_review_decision"
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({ id: "review", object: "chat.completion.chunk", choices: [{ index: 0, delta: done ? { role: "assistant", content: "请核对决定" } : { role: "assistant", tool_calls: [{ index: 0, id: name, type: "function", function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: done ? "stop" : "tool_calls" }] })}\n\ndata: [DONE]\n\n`,
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
    expect(
      (
        await request.put(`${base}/model-setting`, {
          headers,
          data: {
            enabled: true,
            kind: "openai_compatible",
            provider: "openai-compatible",
            model: "synthetic",
            base_url: `http://${host}:${address.port}/v1`,
            api_key: "synthetic-key",
          },
        })
      ).ok(),
    ).toBeTruthy()
    await page.goto("/login")
    await page.evaluate(
      ({ accessToken, orgId }) => {
        localStorage.setItem("access_token", accessToken)
        localStorage.setItem("current_org_id", orgId)
      },
      { accessToken: managerToken, orgId: admin.org_id },
    )
    await page.goto("/assistant")
    const panel = page.getByRole("region", { name: "助手", exact: true })
    await panel.getByLabel("发给机器人的消息").fill("列出待审批并批准")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel.getByRole("region", { name: "审批决定复述" })
    await expect(card).toContainText("合成能力1")
    await expect(card).toContainText("决定：已批准")
    await expect(card).toContainText("等级：独立完成")
    await expect(card).not.toContainText("routine")
    await expect(card).not.toContainText("隐藏的受限自述")
    await expect(
      card.getByRole("link", { name: "去审批页查看证据" }),
    ).toHaveAttribute("href", `/reviews/${draft.id}`)
    await card.getByRole("button", { name: "确认决定", exact: true }).click()
    await expect(
      panel.getByText("审批决定已保存", { exact: false }),
    ).toBeVisible()
    await page.goto("/reviews")
    await expect(
      page.getByRole("heading", { name: "待审批", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("listitem").filter({ hasText: "合成员工" }),
    ).toHaveCount(0)
    const processed = await (
      await request.get(`${base}/me/requests?status=all`, {
        headers: ownerHeaders,
      })
    ).json()
    expect(
      processed.data.find((row: { id: string }) => row.id === draft.id).status,
    ).toBe("approved")
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
