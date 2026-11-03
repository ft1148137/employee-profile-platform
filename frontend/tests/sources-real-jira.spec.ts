import { expect, test } from "@playwright/test"
import { createSourcesOrg, saveCloud, syncJira } from "./utils/sources"

test.use({
  storageState: { cookies: [], origins: [] },
  trace: "off",
  screenshot: "off",
  video: "off",
})
test("real Cloud connection and synchronization through the settings page", async ({
  page,
}) => {
  test.skip(
    !process.env.JIRA_BASE_URL ||
      !process.env.JIRA_EMAIL ||
      !process.env.JIRA_API_TOKEN,
    "未配置真实 Jira 测试凭据",
  )
  test.setTimeout(180000)
  await createSourcesOrg(page)
  await page.goto("/admin/sources")
  await saveCloud(
    page,
    process.env.JIRA_BASE_URL!,
    process.env.JIRA_EMAIL!,
    process.env.JIRA_API_TOKEN!,
  )
  await page.getByRole("button", { name: "测试连接" }).click()
  await expect(page.getByText(/Jira 连接成功：.+/)).toBeVisible()
  await syncJira(page)
  const status = await page
    .getByRole("status")
    .filter({ hasText: "Jira 同步：成功" })
    .textContent()
  const records = Number(status?.match(/记录 (\d+)/)?.[1])
  expect(records).toBeGreaterThanOrEqual(60)
  console.log(`真实 Jira 同步记录数：${records}`)
})
