import { readFileSync } from "node:fs"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("upload a sample roster in chat, restore preview and confirm import", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  test.setTimeout(90000)
  const email = `roster-chat-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "名单对话合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id, access_token } = await initialized.json()
  const base = `/api/v1/orgs/${org_id}`
  const headers = { Authorization: `Bearer ${access_token}` }
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
  const initial = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "旧名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,邮箱\nOLD,合成离职员工,旧部门,old@synthetic.example.com\n",
        ),
      },
    },
  })
  const old = await initial.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${old.id}/apply`, {
        headers,
        data: { base_revision: old.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page.setViewportSize({ width: 390, height: 844 })
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("帮我导入名单")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.getByRole("form", { name: "上传名单" })).toBeVisible({
    timeout: 30000,
  })
  await page.getByLabel("选择员工名单").setInputFiles({
    name: "超大名单.csv",
    mimeType: "text/csv",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
  })
  await page.getByRole("button", { name: "上传并预览" }).click()
  await expect(page.getByRole("alert")).toContainText("文件超过 5 MB")
  await page.getByLabel("选择员工名单").setInputFiles({
    name: "示例名单.csv",
    mimeType: "text/csv",
    buffer: readFileSync(
      new URL("../../examples/roster/sample-30.csv", import.meta.url),
    ),
  })
  await page.getByRole("button", { name: "上传并预览" }).click()
  const preview = page.getByRole("region", { name: "名单预览" })
  await expect(preview.getByText("新增 30 人", { exact: true })).toBeVisible()
  await expect(preview.getByText("停用 1 人", { exact: true })).toBeVisible()
  await page.reload()
  await expect(preview.getByText("新增 30 人", { exact: true })).toBeVisible()
  const history = async () =>
    (await (await request.get(`${base}/roster/imports`, { headers })).json())
      .data
  expect(
    (await history()).filter(
      (item: { status: string }) => item.status === "applied",
    ),
  ).toHaveLength(1)
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(
    (await history()).filter(
      (item: { status: string }) => item.status === "applied",
    ),
  ).toHaveLength(2)
  await expect(
    card.getByRole("button", { name: "撤销", exact: true }),
  ).toHaveCount(0)
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("roster-chat-applied.png"),
    fullPage: true,
  })
})
