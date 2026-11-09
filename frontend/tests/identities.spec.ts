import path from "node:path"
import { expect, test } from "@playwright/test"
import type { ExternalIdentitiesPublic } from "../src/client"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("confirm candidates in bulk, select employee, unlink and save optional setup step", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000)
  const response = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(response.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await response.json()).token,
      org_name: "合成账号对应测试企业",
      admin_username: `identities-${crypto.randomUUID()}@example.com`,
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const base = `/api/v1/orgs/${org_id}`
  const headers = { Authorization: `Bearer ${access_token}` }
  await page.goto("/login")
  await page.evaluate(
    ({ access_token, org_id }) => {
      localStorage.setItem("access_token", access_token)
      localStorage.setItem("current_org_id", org_id)
    },
    { access_token, org_id },
  )
  await page.goto("/admin/roster")
  await page
    .getByLabel("拖拽名单到这里，或选择文件上传")
    .setInputFiles(
      path.resolve(
        import.meta.dirname,
        "../../examples/test-enterprises/qiguang-aiot/roster.csv",
      ),
    )
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  expect(
    (
      await request.put(`${base}/sources/jira`, {
        headers,
        data: {
          deployment: "cloud",
          base_url: "http://mock-jira:8080/qiguang-aiot",
          auth_type: "api_token",
          username: "jira-admin@example.com",
          secret: "mock-cloud-token",
          start_date: "2024-01-01",
        },
      })
    ).ok(),
  ).toBeTruthy()
  const sync = await request.post(`${base}/sources/jira/sync`, { headers })
  expect(sync.ok()).toBeTruthy()
  const runId = (await sync.json()).id
  await expect
    .poll(
      async () => {
        const runs = await (
          await request.get(`${base}/sync-runs`, { headers })
        ).json()
        return runs.data.find((run: { id: string }) => run.id === runId)?.status
      },
      { timeout: 30000 },
    )
    .toBe("succeeded")
  const automatic: ExternalIdentitiesPublic = await (
    await request.get(`${base}/external-identities?status=linked`, { headers })
  ).json()
  expect(automatic.count).toBeGreaterThanOrEqual(3)
  await page.goto("/admin/identities")
  await page.getByRole("tab", { name: `已关联（${automatic.count}）` }).click()
  await expect(
    page.getByText("按邮箱自动关联", { exact: true }).first(),
  ).toBeVisible()
  for (const row of automatic.data) {
    expect(row.linked_by).toBe("system_worker:email_match")
    const unlinked = await request.post(
      `${base}/external-identities/${row.id}/unlink`,
      { headers },
    )
    expect(unlinked.ok()).toBeTruthy()
  }
  // Administrator opt-outs survive another synchronization; candidates stay usable.
  const repeated = await request.post(`${base}/sources/jira/sync`, { headers })
  expect(repeated.ok()).toBeTruthy()
  const repeatedId = (await repeated.json()).id
  await expect
    .poll(
      async () => {
        const runs = await (
          await request.get(`${base}/sync-runs`, { headers })
        ).json()
        return runs.data.find((run: { id: string }) => run.id === repeatedId)
          ?.status
      },
      { timeout: 30000 },
    )
    .toBe("succeeded")
  const afterRepeat = await (
    await request.get(`${base}/external-identities?status=linked`, { headers })
  ).json()
  expect(afterRepeat.count).toBe(0)
  const data: ExternalIdentitiesPublic = await (
    await request.get(`${base}/external-identities`, { headers })
  ).json()
  const candidates = data.data.filter(
    (row) => row.candidate?.reason === "email",
  )
  expect(candidates.length).toBeGreaterThanOrEqual(3)
  const [first, second, manual] = candidates
  expect(manual.candidate!.id).not.toBe(first.candidate!.id)
  await page.goto("/admin/identities")
  await expect(
    page.getByRole("tab", { name: `未关联（${data.count}）` }),
  ).toBeVisible()
  await expect(
    page.getByText("邮箱相同", { exact: true }).first(),
  ).toBeVisible()
  await page.getByLabel("按来源筛选").selectOption("jira")
  await page.getByLabel("搜索账号、名字或邮箱").fill(first.email!)
  await expect(
    page.getByRole("row").filter({ hasText: first.external_id }),
  ).toHaveCount(1)
  await expect(
    page.getByRole("row").filter({ hasText: second.external_id }),
  ).toHaveCount(0)
  await page.getByLabel("搜索账号、名字或邮箱").fill("")
  await page
    .getByRole("checkbox", {
      name: `选择账号 ${first.external_id}`,
      exact: true,
    })
    .check()
  await page
    .getByRole("checkbox", {
      name: `选择账号 ${second.external_id}`,
      exact: true,
    })
    .check()
  const linked = page.waitForRequest(
    (req) =>
      req.url().endsWith("/external-identities/link") &&
      req.method() === "POST",
  )
  await page.getByRole("button", { name: "确认所选候选（2）" }).click()
  expect((await linked).postDataJSON().links).toHaveLength(2)
  await expect(page.getByRole("status")).toContainText(/已补充 [1-9]\d* 条证据/)
  await page.getByRole("tab", { name: "已关联（2）" }).click()
  await expect(
    page.getByRole("row").filter({ hasText: first.external_id }),
  ).toContainText(first.candidate!.display_name)
  await expect(
    page.getByRole("row").filter({ hasText: first.external_id }),
  ).toContainText(/20\d{2}/)
  await page.getByRole("tab", { name: /未关联/ }).click()
  const manualRow = page
    .getByRole("row")
    .filter({ hasText: manual.external_id })
  await manualRow.getByRole("button", { name: "选择员工" }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByLabel("搜索员工姓名或工号")
    .fill(first.candidate!.employee_code!)
  await expect(dialog.getByRole("button", { name: /在职/ })).toContainText(
    first.candidate!.department!.name,
  )
  await page.route("**/external-identities/link", (route) =>
    route.fulfill({
      status: 409,
      json: { detail: "账号已关联到其他员工，请先解除" },
    }),
  )
  await dialog.getByRole("button", { name: /在职/ }).click()
  await expect(dialog.getByRole("alert")).toHaveText(
    "账号已关联到其他员工，请先解除",
  )
  await page.unroute("**/external-identities/link")
  await dialog.getByRole("button", { name: /在职/ }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole("tab", { name: "已关联（3）" }).click()
  await expect(
    page.getByRole("row").filter({ hasText: manual.external_id }),
  ).toContainText(first.candidate!.display_name)
  const firstRow = page.getByRole("row").filter({ hasText: first.external_id })
  await firstRow.getByRole("button", { name: "解除关联" }).click()
  await expect(dialog).toContainText("并留下记录。可以重新关联。")
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  await expect(firstRow).toBeVisible()
  await firstRow.getByRole("button", { name: "解除关联" }).click()
  await dialog.getByRole("button", { name: "确认解除" }).click()
  await expect(page.getByRole("status")).toContainText(/已撤销 \d+ 条证据/)
  await page.getByRole("tab", { name: /未关联/ }).click()
  await expect(
    page.getByRole("row").filter({ hasText: first.external_id }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("identities.png"),
    fullPage: true,
  })
  await page.goto("/setup-wizard")
  const step = page
    .getByRole("navigation", { name: "设置步骤" })
    .getByRole("button", { name: /确认账号对应/ })
  await step.click()
  await expect(
    page.getByText(/还有 \d+ 个账号未关联，它们的记录不会算到任何人头上/),
  ).toBeVisible()
  await page.getByRole("button", { name: "跳过", exact: true }).click()
  await expect(step).toContainText("已跳过")
  await page.reload()
  await expect(step).toContainText("已跳过")
  await step.click()
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await expect(step).toContainText("已完成")
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/admin/identities")
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
})

for (const username of ["manager.platform", "employee.platform"]) {
  test(`${username} cannot access external identities`, async ({ page }) => {
    await logInUser(
      page,
      `${username}@demo.example.com`,
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(
      page.getByRole("link", { name: "人员", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/identities")
    await expect(page.getByRole("alert")).toHaveText("需要企业管理员权限")
  })
}
