import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("employee sees character counts and cannot preview oversized text", async ({
  page,
}) => {
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.getByRole("link", { name: "组织", exact: true }).click()
  await page.locator("summary", { hasText: /待你确认：初始画像草稿/ }).click()
  await page
    .getByRole("region", { name: "初始画像草稿", exact: true })
    .getByRole("button", { name: "修改", exact: true })
    .first()
    .click()
  const dialog = page.getByRole("dialog")
  const statement = dialog
    .getByRole("textbox", { name: "能力说明", exact: true })
    .first()
  await statement.fill("😀".repeat(201))
  await expect(dialog.getByText("201 / 200 字", { exact: true })).toBeVisible()
  await expect(dialog.getByRole("alert")).toContainText(
    "能力说明最多 200 字，第 1 条有 201 字",
  )
  await expect(
    dialog.getByRole("button", { name: "预览并提交", exact: true }),
  ).toBeDisabled()
  await statement.fill("😀".repeat(200))
  await expect(
    dialog.getByRole("button", { name: "预览并提交", exact: true }),
  ).toBeEnabled()
  await dialog
    .getByLabel("补充说明", { exact: true })
    .first()
    .fill("中".repeat(4001))
  await expect(
    dialog.getByText("4001 / 4000 字", { exact: true }),
  ).toBeVisible()
  await expect(dialog.getByRole("alert")).toContainText("说明最多 4000 字")
  await expect(
    dialog.getByRole("button", { name: "预览并提交", exact: true }),
  ).toBeDisabled()
})
