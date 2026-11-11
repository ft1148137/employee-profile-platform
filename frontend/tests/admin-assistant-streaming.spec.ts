import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

declare global {
  interface Window {
    reply: { emit: (event: object) => void; finish: () => void }
  }
}

test.use({ storageState: { cookies: [], origins: [] } })

test("admin streams, folds tool progress, stops, sends again and hides pasted secrets", async ({
  page,
}) => {
  const session = crypto.randomUUID()
  await page.route("**/admin-assistant/availability?*", (route) =>
    route.fulfill({ json: { available: true, reason: "" } }),
  )
  await page.route("**/admin-assistant/sessions/*/messages?*", (route) =>
    route.fulfill({ json: [] }),
  )
  // Gate each synthetic model fragment on a UI assertion, without timing races.
  await page.addInitScript(() => {
    const original = window.fetch
    window.fetch = async (input, init) => {
      if (
        typeof input !== "string" ||
        !input.includes("/admin-assistant/sessions/") ||
        !input.includes("/turns?")
      )
        return original(input, init)
      if (
        !new Headers(init?.headers).get("Authorization")?.startsWith("Bearer ")
      )
        throw new Error("Missing bearer token")
      return new Response(
        new ReadableStream({
          start(controller) {
            window.reply = {
              emit(event) {
                const bytes = new TextEncoder().encode(
                  `${JSON.stringify(event)}\n`,
                )
                // Split inside a Chinese character as well as between NDJSON lines.
                const middle = bytes.indexOf(0xe7) + 1
                controller.enqueue(bytes.slice(0, middle))
                controller.enqueue(bytes.slice(middle))
              },
              finish: () => controller.close(),
            }
            init?.signal?.addEventListener(
              "abort",
              () => controller.error(new DOMException("Stopped", "AbortError")),
              { once: true },
            )
            window.reply.emit({ type: "text", text: "第一段" })
          },
        }),
        { headers: { "Content-Type": "application/x-ndjson" } },
      )
    }
  })
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto(`/admin/assistant?session=${session}`)
  const panel = page.getByRole("region", { name: "管理助手", exact: true })
  const input = panel.getByLabel("发给管理助手的消息")
  await expect(input).toBeEnabled()
  await input.fill("合成问题")
  await input.press("Shift+Enter")
  await expect(input).toHaveValue("合成问题\n")
  await input.press("Enter")
  await expect(panel.getByRole("log")).toContainText("合成问题")
  await expect(panel.getByText("第一段", { exact: true })).toBeVisible()
  await expect(
    panel.getByText("第一段第二段第三段", { exact: true }),
  ).toHaveCount(0)
  await page.evaluate(() => {
    window.reply.emit({
      type: "tool",
      name: "查看员工及账号状态",
      phase: "start",
      ok: true,
    })
    window.reply.emit({
      type: "tool",
      name: "查看员工及账号状态",
      phase: "end",
      ok: true,
    })
    window.reply.emit({ type: "text", text: "第二段" })
  })
  await expect(panel.getByText("第一段第二段", { exact: true })).toBeVisible()
  const toggle = panel.getByText("调用了 1 个功能", { exact: true })
  await expect(
    panel.getByText("查看员工及账号状态：成功", { exact: true }),
  ).toBeHidden()
  await toggle.click()
  await expect(
    panel.getByText("查看员工及账号状态：成功", { exact: true }),
  ).toBeVisible()
  await toggle.click()
  await expect(
    panel.getByText("查看员工及账号状态：成功", { exact: true }),
  ).toBeHidden()
  await page.evaluate(() => {
    window.reply.emit({ type: "text", text: "第三段" })
    window.reply.emit({
      type: "final",
      response: { messages: [{ type: "text", text: "第一段第二段第三段" }] },
    })
    window.reply.finish()
  })
  await expect(
    panel.getByText("第一段第二段第三段", { exact: true }),
  ).toBeVisible()
  await expect(input).toBeEnabled()
  await input.fill("停止这轮")
  await input.press("Enter")
  await expect(panel.getByText("第一段", { exact: true })).toBeVisible()
  await panel.getByRole("button", { name: "停止", exact: true }).click()
  await expect(panel.getByText("已停止", { exact: true })).toBeVisible()
  await expect(panel.getByText("第一段", { exact: true })).toHaveCount(0)
  await expect(panel.getByText("停止这轮", { exact: true })).toHaveCount(0)
  await input.fill("再发一条")
  await input.press("Enter")
  await expect(panel.getByText("第一段", { exact: true })).toBeVisible()
  await page.evaluate(() => {
    window.reply.emit({
      type: "final",
      response: { messages: [{ type: "text", text: "继续回复成功" }] },
    })
    window.reply.finish()
  })
  await expect(panel.getByText("继续回复成功", { exact: true })).toBeVisible()
  await expect(input).toBeEnabled()
  const secret = "sk-synthetic-secret-never-rendered"
  await input.fill(secret)
  await input.press("Enter")
  await expect(panel.getByRole("log")).toContainText("已隐藏可能的密钥")
  await expect(panel.getByRole("log")).not.toContainText(secret)
  await page.evaluate(() => {
    window.reply.emit({
      type: "final",
      response: { messages: [{ type: "text", text: "请用安全输入框填写" }] },
    })
    window.reply.finish()
  })
  await expect(input).toBeEnabled()
  await expect(panel.getByRole("log")).toContainText("请用安全输入框填写")
  await expect(panel.getByRole("log")).not.toContainText(secret)
})
