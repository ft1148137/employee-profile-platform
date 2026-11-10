import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { test as base, expect, type Page } from "@playwright/test"

export { expect } from "@playwright/test"
export const jiraTestUrl =
  process.env.JIRA_TEST_URL ?? "http://mock-jira:8080/demo-org"

export const dictionaryDefinitions = [
  {
    key: "synthetic",
    name: "合成能力",
    category: "工程",
    description: "合成能力说明",
    ai_inference: true,
    level_rubric: {
      levels: [{ key: "basic", label: "基础", description: "在指导下完成" }],
    },
  },
]

// The worker calls this local transport; no external model is used in E2E.
export const test = base.extend<{ modelUrl: string }>({
  modelUrl: async ({ baseURL }, use) => {
    let botRequestId: string | undefined
    const server = createServer(async (req, res) => {
      let raw = ""
      for await (const chunk of req) raw += chunk
      const request = JSON.parse(raw)
      const admin = request.tools?.some(
        (tool: { function: { name: string } }) =>
          tool.function.name === "propose_changes",
      )
      const chat =
        request.tools?.some(
          (tool: { function: { name: string } }) =>
            tool.function.name === "get_my_requests",
        ) || admin
      const last = request.messages.at(-1)
      if (chat && !admin && last?.role === "tool") {
        const result = JSON.parse(last.content)
        if (result.data) {
          botRequestId = result.data.find(
            (row: { status: string }) =>
              row.status === "draft" || row.status === "pending_owner",
          )?.id
        }
      }
      const toolResult =
        request.messages.at(-1)?.role === "tool" || !request.tools?.length
      const jiraRequest =
        admin &&
        request.messages.some(
          (message: { role: string; content: unknown }) =>
            message.role === "user" &&
            JSON.stringify(message.content).includes("修改 Jira 站点"),
        )
      const jiraSecret =
        jiraRequest &&
        toolResult &&
        JSON.stringify(last.content).includes("changes")
      if (jiraRequest) expect(raw).not.toContain("mock-cloud-token")
      const finish = toolResult && !jiraSecret
      const content = request.messages.find(
        (m: { role: string }) => m.role === "user",
      ).content
      const body = (
        request.tools?.length && !chat
          ? JSON.parse(typeof content === "string" ? content : content[0].text)
          : { capabilities: [], records: [] }
      ) as {
        capabilities: { key: string }[]
        records: { ref: string }[]
      }
      const items =
        body.records.length && body.capabilities.length
          ? [
              {
                capability_key: body.capabilities[0].key,
                statement: "合成模型能力建议",
                level: null,
                evidence: body.records.map((r) => r.ref),
                explanation: "合成工作记录",
                uncertainty: "需要本人确认",
              },
            ]
          : []
      res.writeHead(200, { "Content-Type": "text/event-stream" })
      const chunk = {
        id: "synthetic-completion",
        object: "chat.completion.chunk",
        created: 1,
        model: request.model,
        choices: [
          {
            index: 0,
            delta: finish
              ? {
                  role: "assistant",
                  content: admin
                    ? "请核对确认卡，点击后才生效。"
                    : chat
                      ? "已提交"
                      : "已完成",
                }
              : {
                  role: "assistant",
                  tool_calls: [
                    {
                      index: 0,
                      id: "synthetic-draft",
                      type: "function",
                      function: {
                        name: admin
                          ? "propose_changes"
                          : chat
                            ? JSON.stringify(last).includes("补充")
                              ? "draft_update_request"
                              : "get_my_requests"
                            : "submit_draft",
                        arguments: JSON.stringify(
                          admin
                            ? {
                                items: [
                                  {
                                    function: JSON.stringify(last).includes(
                                      "重置密码",
                                    )
                                      ? "reset_password"
                                      : JSON.stringify(last).includes(
                                            "办理离职",
                                          )
                                        ? "offboard_employees"
                                        : JSON.stringify(last).includes(
                                              "关闭部门自动起草",
                                            )
                                          ? "update_drafting_settings"
                                          : JSON.stringify(last).includes(
                                                "关闭经理确认",
                                              )
                                            ? "update_approval_policy"
                                            : JSON.stringify(last).includes(
                                                  "修改能力说明",
                                                )
                                              ? "update_capability_dictionary"
                                              : "update_update_settings",
                                    args: JSON.stringify(last).includes(
                                      "重置密码",
                                    )
                                      ? {
                                          user_id: JSON.stringify(last).match(
                                            /[0-9a-f]{8}-[0-9a-f-]{27,}/,
                                          )?.[0],
                                        }
                                      : JSON.stringify(last).includes(
                                            "办理离职",
                                          )
                                        ? {
                                            employee_ids: [
                                              JSON.stringify(last).match(
                                                /[0-9a-f]{8}-[0-9a-f-]{27,}/,
                                              )?.[0],
                                            ],
                                          }
                                        : JSON.stringify(last).includes(
                                              "关闭部门自动起草",
                                            )
                                          ? {
                                              excluded_capability_keys: [],
                                              disabled_department_ids: [
                                                JSON.stringify(last).match(
                                                  /[0-9a-f]{8}-[0-9a-f-]{27,}/,
                                                )?.[0],
                                              ],
                                            }
                                          : JSON.stringify(last).includes(
                                                "关闭经理确认",
                                              )
                                            ? { require_manager: false }
                                            : JSON.stringify(last).includes(
                                                  "修改能力说明",
                                                )
                                              ? {
                                                  definitions:
                                                    dictionaryDefinitions.map(
                                                      (definition) => ({
                                                        ...definition,
                                                        description:
                                                          "助手修改后的能力说明",
                                                      }),
                                                    ),
                                                  expected_version: 1,
                                                  redraft: false,
                                                }
                                              : {
                                                  reminder_schedule: "yearly",
                                                  stale_after_months: 36,
                                                },
                                  },
                                ],
                              }
                            : chat
                              ? JSON.stringify(last).includes("补充")
                                ? {
                                    ...(botRequestId
                                      ? { request_id: botRequestId }
                                      : {}),
                                    items: [
                                      {
                                        op: "add",
                                        capability_key: "automated_testing",
                                        statement: "我补充了一段合成项目经历",
                                        owner_statement:
                                          "本人完成了合成项目的验证",
                                      },
                                    ],
                                  }
                                : {}
                              : { items },
                        ),
                      },
                    },
                  ],
                },
            finish_reason: null as string | null,
          },
        ],
      }
      const calls = chunk.choices[0].delta.tool_calls
      if (admin && !toolResult && JSON.stringify(last).includes("换模型")) {
        const tool = calls?.[0]
        if (tool) {
          tool.function = {
            name: "propose_changes",
            arguments: JSON.stringify({
              items: [
                {
                  function: "update_model_setting",
                  args: {
                    enabled: true,
                    kind: "openai_compatible",
                    provider: "openai-compatible",
                    model: "synthetic-updated",
                    base_url: `http://${req.headers.host}/v1`,
                  },
                },
              ],
            }),
          }
        }
      }
      if (jiraRequest && calls) {
        calls[0].id = jiraSecret ? "synthetic-secret" : "synthetic-jira"
        calls[0].function = {
          name: jiraSecret ? "request_secret_input" : "propose_changes",
          arguments: JSON.stringify(
            jiraSecret
              ? { function: "update_jira_connection", field: "secret" }
              : {
                  items: [
                    {
                      function: "update_jira_connection",
                      args: {
                        deployment: "cloud",
                        base_url: jiraTestUrl,
                        auth_type: "api_token",
                        username: "jira-admin@example.com",
                        start_date: "2026-01-01",
                      },
                    },
                  ],
                },
          ),
        }
      }
      if (admin && !toolResult && JSON.stringify(last).includes("导入名单")) {
        const tool = chunk.choices[0].delta.tool_calls?.[0]
        if (tool) {
          tool.function.name = "request_file"
          tool.function.arguments = JSON.stringify({ kind: "roster" })
        }
      }
      res.write(`data: ${JSON.stringify(chunk)}\n\n`)
      const finished = {
        ...chunk,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: finish ? "stop" : "tool_calls",
          },
        ],
      }
      res.end(`data: ${JSON.stringify(finished)}\n\ndata: [DONE]\n\n`)
    })
    await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing mock address")
    const host =
      process.env.MODEL_TEST_HOST ??
      (baseURL?.includes("backend:")
        ? Object.values(networkInterfaces())
            .flat()
            .find((network) => network?.family === "IPv4" && !network.internal)
            ?.address
        : "localhost")
    try {
      await use(`http://${host}:${address.port}/v1`)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      )
    }
  },
})

export async function configureModel(page: Page, modelUrl: string) {
  await page.getByLabel("模型类型").selectOption("openai_compatible")
  await page.getByLabel("启用模型").check()
  await page.getByLabel("模型名", { exact: true }).fill("synthetic")
  await page.getByLabel(/base URL/).fill(modelUrl)
  await page.getByLabel("密钥", { exact: false }).first().fill("synthetic-key")
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(
    page.getByText("模型设置已保存。", { exact: true }),
  ).toBeVisible()
}
