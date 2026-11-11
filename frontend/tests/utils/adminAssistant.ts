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
  const button = page.getByRole("button", { name: /展开导航|收起导航/ })
  const newConversation = page.getByRole("link", {
    name: "新对话",
    exact: true,
  })
  if (
    (page.viewportSize()?.width ?? 1440) < 768 &&
    !(await newConversation.isVisible())
  )
    await button.click()
  await expect(newConversation).toBeVisible()
  if (id) await page.goto(`/admin/assistant?session=${id}`)
  else await newConversation.click()
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
