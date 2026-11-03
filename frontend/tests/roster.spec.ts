import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

test("roster errors, correction, transfer, mass deactivation and stale preview at 375px", async ({
  page,
  request,
}, testInfo) => {
  const response = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await response.json()).token,
      org_name: "合成名单测试企业",
      admin_username: `roster-${crypto.randomUUID()}`,
      admin_full_name: "名单管理员",
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  await page.goto("/login")
  await page.evaluate(
    ({ token, orgId }) => {
      localStorage.setItem("access_token", token)
      localStorage.setItem("current_org_id", orgId)
    },
    { token: access_token, orgId: org_id },
  )
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/admin/roster")
  const upload = page.getByLabel("拖拽名单到这里，或选择文件上传")
  const file = (content: string) => ({
    name: "名单.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(content),
  })
  const header = "工号,姓名,部门,职位,上级工号,邮箱\n"
  await upload.setInputFiles(file(`${header}001,,研发部,,,\n`))
  await expect(page.getByRole("alert")).toContainText("名单有错误")
  await expect(
    page.getByRole("cell", { name: "姓名", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "确认导入", exact: true }),
  ).toBeDisabled()
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("roster-errors.png"),
    fullPage: true,
  })
  const original = `${header}${Array.from({ length: 8 }, (_, index) => `00${index + 1},合成员工${index + 1},研发部,,,`).join("\n")}`
  await upload.setInputFiles(file(original))
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  const changed = `${header}001,合成员工1,支持部,,,\n002,合成员工2,研发部,,,`
  await upload.setInputFiles(file(changed))
  const preview = page.getByRole("region", { name: "名单预览" })
  await expect(preview.getByText("调岗 1 人", { exact: true })).toBeVisible()
  await expect(preview.getByText("停用 6 人", { exact: true })).toBeVisible()
  await preview.getByText("调岗 1 人", { exact: true }).click()
  await expect(
    preview.getByRole("cell", { name: "部门：研发部", exact: true }),
  ).toBeVisible()
  const confirm = page.getByRole("button", { name: "确认导入", exact: true })
  await expect(confirm).toBeDisabled()
  await page
    .getByRole("checkbox", { name: "我确认这些员工已离职，停用他们的账号" })
    .check()
  // A concurrent import invalidates the revision shown in this preview.
  const headers = { Authorization: `Bearer ${access_token}` }
  const concurrent = await request.post(
    `/api/v1/orgs/${org_id}/roster/imports`,
    { headers, multipart: { file: file(original) } },
  )
  expect(concurrent.ok()).toBeTruthy()
  const concurrentPreview = await concurrent.json()
  expect(
    (
      await request.post(
        `/api/v1/orgs/${org_id}/roster/imports/${concurrentPreview.id}/apply`,
        { headers, data: { base_revision: concurrentPreview.base_revision } },
      )
    ).ok(),
  ).toBeTruthy()
  await confirm.click()
  await expect(page.getByRole("alert").first()).toContainText(
    "名单在你预览之后被更新过，请重新上传",
  )
  await expect(confirm).toBeDisabled()
  await upload.setInputFiles(file(changed))
  await expect(page.getByRole("checkbox")).not.toBeChecked()
  await page.getByRole("checkbox").check()
  await confirm.click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  await expect(
    page
      .getByRole("region", { name: "导入历史" })
      .getByText("已导入", { exact: true })
      .first(),
  ).toBeVisible()
  await expectChineseCopy(page)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await upload.setInputFiles(file(original))
  await expect(
    preview.getByText("恢复在职 6 人", { exact: true }),
  ).toBeVisible()
  const reactivationWarning =
    preview.getByText(/恢复在职的 6 名员工账号仍然停用/)
  await expect(reactivationWarning).toHaveCount(1)
  await expect(reactivationWarning).toBeVisible()
})
