import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const width of [1440, 390]) {
  test(`admin sidebar carries the current page at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.setViewportSize({ width, height: 900 })
    const sent: string[] = []
    await page.route("**/admin-assistant/sessions/*/turns?*", async (route) => {
      sent.push(route.request().postDataJSON().page)
      await route.fulfill({
        json: {
          messages: [
            { type: "text", text: "请查看首页待办。" },
            { type: "link", title: "打开管理首页", page: "/admin" },
          ],
        },
      })
    })
    for (const current of [
      "/admin/people?tab=roster",
      "/admin/data-ai?tab=bot",
    ]) {
      await page.goto(current)
      await page.getByRole("button", { name: "打开管理助手" }).click()
      const dialog = page.getByRole("dialog")
      await expect(
        dialog.getByRole("heading", { name: "管理助手" }),
      ).toBeVisible()
      await dialog
        .getByRole("textbox", { name: "发给管理助手的消息" })
        .fill("今天有什么要处理的")
      await dialog.getByRole("button", { name: "发送", exact: true }).click()
      await expect(
        dialog.getByText("请查看首页待办。", { exact: true }),
      ).toBeVisible()
      await expect(
        dialog.getByRole("link", { name: "打开管理首页" }),
      ).toHaveAttribute("href", "/admin")
      expect(sent[sent.length - 1]).toBe(current)
      await expectChineseCopy(page)
      await page.screenshot({
        path: testInfo.outputPath(`admin-assistant-${sent.length}.png`),
      })
      await dialog.getByRole("button", { name: "关闭", exact: true }).click()
    }
    expect(sent).toHaveLength(2)
    await page.goto("/employees")
    await expect(
      page.getByRole("button", { name: "打开管理助手" }),
    ).toHaveCount(0)
  })
}

test("employee sees their profile bot on a manual page", async ({ page }) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules")
  await expect(page.getByRole("button", { name: "打开管理助手" })).toHaveCount(
    0,
  )
  await expect(
    page.getByRole("button", { name: "打开画像机器人" }),
  ).toBeVisible()
})
