import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("onboard and transfer on the web, then offboard through the assistant", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  const email = `personnel-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "合成人员测试企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
  expect(
    (
      await request.put(`${base}/model-setting`, {
        headers,
        data: {
          enabled: true,
          kind: "openai_compatible",
          provider: "openai-compatible",
          model: "synthetic",
          base_url: modelUrl,
          api_key: "synthetic-key",
        },
      })
    ).ok(),
  ).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/admin/people?tab=accounts")
  await page.getByRole("button", { name: "新增员工", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByLabel("工号", { exact: true }).fill("NEW01")
  await dialog.getByLabel("姓名", { exact: true }).fill("合成员工")
  await dialog
    .getByLabel("邮箱", { exact: true })
    .fill("new-person@example.com")
  await dialog.getByLabel("部门", { exact: true }).fill("研发中心/平台部")
  await dialog.getByLabel("职位", { exact: true }).fill("工程师")
  await dialog.getByRole("button", { name: "预览变动" }).click()
  await expect(dialog.getByRole("region", { name: "名单预览" })).toContainText(
    "合成员工",
  )
  expect(
    (await (await request.get(`${base}/accounts`, { headers })).json()).count,
  ).toBe(0)
  await dialog.getByRole("button", { name: "确认新增员工" }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole("link", { name: "合成员工", exact: true }).click()
  await page.getByRole("button", { name: "调岗", exact: true }).click()
  await expect(dialog.getByLabel("部门", { exact: true })).toHaveValue(
    "研发中心 / 平台部",
  )
  await dialog.getByLabel("部门", { exact: true }).fill("运营部")
  await dialog.getByLabel("职位", { exact: true }).fill("主管")
  await dialog.getByRole("button", { name: "预览变动" }).click()
  await expect(dialog.getByRole("region", { name: "名单预览" })).toContainText(
    "运营部",
  )
  await dialog.getByRole("button", { name: "确认调岗" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
    "主管",
  )
  const employee = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data[0]
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill(`办理离职 ${employee.id}`)
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toContainText("办理离职", { timeout: 30000 })
  await expect(card).toContainText("合成员工")
  await expect(card).toContainText("影响 1 人")
  await expect(card).toContainText("0 个待处理")
  await expect(card).not.toContainText(employee.id)
  await expect(card.getByRole("button", { name: "再次确认" })).toHaveCount(0)
  expect(
    (await (await request.get(`${base}/accounts`, { headers })).json()).data[0]
      .employment_status,
  ).toBe("active")
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  await expect(
    card.getByRole("button", { name: "撤销", exact: true }),
  ).toHaveCount(0)
  expect(
    (await (await request.get(`${base}/accounts`, { headers })).json()).data[0]
      .employment_status,
  ).toBe("inactive")
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("personnel-offboard.png"),
    fullPage: true,
  })
})
