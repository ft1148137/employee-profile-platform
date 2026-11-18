import { createServer } from "node:http"
import { expect, test } from "@playwright/test"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] } })

test("从网页助手核对项目草稿并创建项目需求", async ({ page, request }) => {
  test.setTimeout(90_000)
  const { base, open, adminToken, managerToken, departments } =
    await setupProjectManagers(request)
  const title = "合成客户报表项目"
  const project = {
    title,
    requesting_department_id: departments.A,
    roles: [
      {
        role_name: "接口开发",
        target_department_id: departments.B,
        capability_key: "delivery",
        minimum_level: "routine",
        headcount: 1,
        allocation_percent: 50,
        window_start: "2026-12-01",
        window_end: "2026-12-02",
        description: "实现报表接口",
      },
      {
        role_name: "报表验证",
        target_department_id: departments.C,
        capability_key: "validation",
        headcount: 1,
        allocation_percent: 30,
        window_start: "2026-12-03",
        window_end: "2026-12-04",
        depends_on: [0],
        description: "核对报表结果",
      },
    ],
  }
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const previous = body.messages.at(-1)?.tool_call_id
    const name = !previous
      ? "get_capability_dictionary"
      : previous === "get_capability_dictionary"
        ? "list_capacity_departments"
        : "coordination"
    const done = previous === "coordination"
    const args =
      name === "coordination"
        ? { function: "prepare_project_draft", arguments: project }
        : {}
    const delta = done
      ? { role: "assistant", content: "请核对项目草稿" }
      : {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: name,
              type: "function",
              function: { name, arguments: JSON.stringify(args) },
            },
          ],
        }
    const chunk = {
      id: "project-draft",
      object: "chat.completion.chunk",
      choices: [
        { index: 0, delta, finish_reason: done ? "stop" : "tool_calls" },
      ],
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`)
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  try {
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing synthetic model address")
    const host = process.env.MODEL_TEST_HOST ?? "127.0.0.1"
    const setting = await request.put(`${base}/model-setting`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        enabled: true,
        kind: "openai_compatible",
        provider: "openai-compatible",
        model: "synthetic",
        base_url: `http://${host}:${address.port}/v1`,
        api_key: "synthetic-key",
      },
    })
    expect(setting.ok(), await setting.text()).toBeTruthy()

    await open(page, "A", "/assistant")
    const assistant = page.getByRole("region", { name: "助手", exact: true })
    await assistant
      .getByLabel("发给机器人的消息")
      .fill(
        "起草一个客户报表项目。研发先做接口，12 月 1–2 日一人投入 50%，测试接着验证，12 月 3–4 日一人投入 30%。",
      )
    await assistant.getByRole("button", { name: "发送", exact: true }).click()
    const draft = assistant.getByRole("region", { name: "项目草稿" })
    await expect(draft).toContainText(title)
    await expect(draft).toContainText("前置角色：1. 接口开发")
    await expect(draft).not.toContainText("合成研发经理")
    const before = await request.get(`${base}/capacity/projects`, {
      headers: { Authorization: `Bearer ${managerToken}` },
    })
    expect(before.ok()).toBeTruthy()
    expect(await before.json()).toEqual([])
    await draft.getByRole("button", { name: "确认创建项目" }).click()
    await expect(assistant.getByText("项目已创建。")).toBeVisible()
    await page.goto("/capacity?tab=projects")
    await expect(page.getByText(title, { exact: true })).toBeVisible()
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
