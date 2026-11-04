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
      const body = JSON.parse(request.messages.at(-1).content) as {
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
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(
        JSON.stringify({
          id: "synthetic-completion",
          object: "chat.completion",
          created: 1,
          model: request.model,
          choices: [
            {
              index: 0,
              finish_reason: "stop",
              message: {
                role: "assistant",
                content: JSON.stringify({ items }),
              },
            },
          ],
          usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
        }),
      )
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
  await page.getByLabel("模型名", { exact: true }).fill("openai/synthetic")
  await page.getByLabel(/base URL/).fill(modelUrl)
  await page.getByLabel("密钥", { exact: false }).first().fill("synthetic-key")
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(
    page.getByText("模型设置已保存。", { exact: true }),
  ).toBeVisible()
}
