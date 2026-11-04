import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { test as base, expect, type Page } from "@playwright/test"

export { expect } from "@playwright/test"

// The worker calls this local transport; no external model is used in E2E.
export const test = base.extend<{ modelUrl: string }>({
  modelUrl: async ({ baseURL }, use) => {
    const server = createServer(async (req, res) => {
      let raw = ""
      for await (const chunk of req) raw += chunk
      const request = JSON.parse(raw)
      const toolResult =
        request.messages.at(-1)?.role === "tool" || !request.tools?.length
      const content = request.messages.find(
        (m: { role: string }) => m.role === "user",
      ).content
      const body = (
        request.tools?.length
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
            delta: toolResult
              ? { role: "assistant", content: "已完成" }
              : {
                  role: "assistant",
                  tool_calls: [
                    {
                      index: 0,
                      id: "synthetic-draft",
                      type: "function",
                      function: {
                        name: "submit_draft",
                        arguments: JSON.stringify({ items }),
                      },
                    },
                  ],
                },
            finish_reason: null as string | null,
          },
        ],
      }
      res.write(`data: ${JSON.stringify(chunk)}\n\n`)
      const finished = {
        ...chunk,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: toolResult ? "stop" : "tool_calls",
          },
        ],
      }
      res.end(`data: ${JSON.stringify(finished)}\n\ndata: [DONE]\n\n`)
    })
    await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing mock address")
    const host = baseURL?.includes("backend:")
      ? Object.values(networkInterfaces())
          .flat()
          .find((network) => network?.family === "IPv4" && !network.internal)
          ?.address
      : "localhost"
    try {
      await use(`http://${host}:${address.port}/v1`)
    } finally {
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
