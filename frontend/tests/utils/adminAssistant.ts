import { expect, type Page } from "@playwright/test"

const manualPages = new WeakMap<Page, string>()

export async function openAdminAssistant(page: Page) {
  if (new URL(page.url()).pathname === "/admin/assistant") return
  manualPages.set(page, page.url())
  const id = await page.evaluate(
    () =>
      Object.entries(localStorage).find(([key]) =>
        key.startsWith("admin-assistant:"),
      )?.[1],
  )
  if (id) await page.goto(`/admin/assistant?session=${id}`)
  else await page.getByRole("link", { name: "聊天", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "管理助手", exact: true }),
  ).toBeVisible()
}

export async function returnToManualPage(page: Page) {
  const target = manualPages.get(page)
  if (!target) throw new Error("Open the assistant from a manual page first")
  const url = new URL(target)
  const link = page.getByRole("link", { name: "打开对应页面", exact: true })
  const matching = link
    .and(page.locator(`a[href="${url.pathname}${url.search}"]`))
    .last()
  if (await matching.count()) await matching.click()
  else await page.goto(target)
}

export async function mockAdminMessages(page: Page) {
  const replies = new Map<string, unknown>()
  await page.route("**/admin-assistant/sessions/*/messages?*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[5]
    const reply = replies.get(id)
    return route.fulfill({
      json: reply
        ? [
            {
              id: crypto.randomUUID(),
              role: "assistant",
              payload: reply,
              created_at: new Date().toISOString(),
            },
          ]
        : [],
    })
  })
  return (url: string, reply: unknown) =>
    replies.set(new URL(url).pathname.split("/")[5], reply)
}
