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
      admin_username: `roster-${crypto.randomUUID()}@example.com`,
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
  const original = `${header}${Array.from({ length: 8 }, (_, index) => `00${index + 1},合成员工${index + 1},研发部,,,e${index + 1}@example.com`).join("\n")}`
  await upload.setInputFiles(file(original))
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  const changed = `${header}001,合成员工1,支持部,,,e1@example.com\n002,合成员工2,研发部,,,e2@example.com`
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

test("opt-in work attributes import and personnel tenure", async ({
  page,
  request,
}) => {
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(token.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成员工档案企业",
      admin_username: `fields-${crypto.randomUUID()}@example.com`,
      admin_full_name: "档案管理员",
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
  await page.goto("/admin/people?tab=roster")
  const switches = page.getByRole("region", { name: "员工档案字段" })
  for (const label of ["岗位序列", "工作地点", "入职日期"]) {
    const toggle = switches.getByRole("switch", { name: label, exact: true })
    await expect(toggle).not.toBeChecked()
    await expect(toggle).toBeEnabled()
    await toggle.click()
    await expect(toggle).toBeChecked()
    await expect(toggle).toBeEnabled()
  }
  const hireYear = new Date().getFullYear() - 3
  await page.getByLabel("拖拽名单到这里，或选择文件上传").setInputFiles({
    name: "员工档案.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      `工号,姓名,部门,职位,上级工号,邮箱,岗位序列,工作地点,入职日期\nE1,合成档案员工,研发,工程师,,fields@example.com,技术,上海,${hireYear}-01-01\nE2,合成新员工,研发,工程师,,recent@example.com,技术,上海,${hireYear + 3}-01-01`,
    ),
  })
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  const accounts = await request.get(`/api/v1/orgs/${org_id}/accounts`, {
    headers: { Authorization: `Bearer ${access_token}` },
  })
  expect(accounts.ok()).toBeTruthy()
  const people = (await accounts.json()).data
  await page.goto(`/admin/people/${people[0].id}`)
  const info = page.getByRole("region", { name: "员工信息" })
  await expect(info.getByText("技术", { exact: true })).toBeVisible()
  await expect(info.getByText("上海", { exact: true })).toBeVisible()
  await expect(info.getByText("入职 3 年", { exact: true })).toBeVisible()
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0)
  await expect(page.getByText("正在加载账号…", { exact: true })).toHaveCount(0)
  await expect(
    page.getByText("正在加载修改记录…", { exact: true }),
  ).toHaveCount(0)
  await page.screenshot({
    path: "../docs/screenshots/m4.2/employee-fields.png",
    fullPage: true,
  })
  await page.goto(`/admin/people/${people[1].id}`)
  await expect(page.getByText("不满 1 年", { exact: true })).toBeVisible()
})
