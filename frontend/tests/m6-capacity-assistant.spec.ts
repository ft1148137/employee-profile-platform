import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("employee confirms a capacity request in the assistant", async ({
  page,
  request,
  baseURL,
}) => {
  const password = "synthetic-capacity-password-2026"
  const setup = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setup.json()).token,
      org_name: "合成容量助手企业",
      admin_username: `capacity-${crypto.randomUUID()}@example.com`,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const preview = await (
    await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,职位,上级工号,邮箱\nE,合成员工,研发部,工程师,,capacity-employee-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
  ).json()
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
      await request.put(`${base}/capacity-settings`, {
        headers,
        data: { enabled: true },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const account = accounts.find((value) => value.employee_code === "E")!
  const reset = await (
    await request.post(
      `${base}/accounts/${account.account!.user_id}/reset-password`,
      { headers },
    )
  ).json()
  const login = await (
    await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
  ).json()
  const changed = await request.patch("/api/v1/users/me/password", {
    headers: { Authorization: `Bearer ${login.access_token}` },
    data: { current_password: reset.initial_password, new_password: password },
  })
  expect(changed.ok()).toBeTruthy()
  const accessToken = (await changed.json()).access_token
  const employeeHeaders = { Authorization: `Bearer ${accessToken}` }
  const week = (
    await (
      await request.get(`${base}/capacity/workloads/me`, {
        headers: employeeHeaders,
      })
    ).json()
  ).weeks[0].week_start
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const done = body.messages.at(-1).role === "tool"
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({
        id: "capacity",
        object: "chat.completion.chunk",
        choices: [
          {
            index: 0,
            delta: done
              ? { role: "assistant", content: "请核对申请" }
              : {
                  role: "assistant",
                  tool_calls: [
                    {
                      index: 0,
                      id: "prepare",
                      type: "function",
                      function: {
                        name: "prepare_workload_request",
                        arguments: JSON.stringify({
                          requested_percent: 80,
                          reason: "合成项目占用",
                          start_week: week,
                          end_week: week,
                        }),
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
      { accessToken, orgId: admin.org_id },
    )
    await page.goto("/assistant")
    const panel = page.getByRole("region", { name: "助手", exact: true })
    await panel.getByLabel("发给助手的消息").fill("请申请调整负荷到 80%")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel.getByRole("region", {
      name: "申请调整负荷",
      exact: true,
    })
    await expect(card).toContainText("其他工作占比：80%")
    await expect(card).toContainText("合成项目占用")
    const read = async () =>
      (
        await (
          await request.get(`${base}/capacity/workloads/me`, {
            headers: employeeHeaders,
          })
        ).json()
      ).requests
    expect(await read()).toHaveLength(0)
    await card.getByRole("button", { name: "确认", exact: true }).click()
    await expect(card.last().getByRole("status")).toContainText(
      "负荷调整申请已提交",
    )
    await expect(card.last().getByRole("button")).toHaveCount(0)
    expect(await read()).toHaveLength(1)
    await page.reload()
    await expect(card.last()).toContainText("等待经理审批")
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
