import type { APIRequestContext } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

async function createOrg(request: APIRequestContext, modelUrl: string) {
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
  return { email, password, base, headers }
}

test("draft roster from materials, restore and confirm without offboarding omitted people", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
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
  await page.goto("/admin/roster")
  await expect(page).toHaveURL("/admin/assistant")
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByLabel("选择对话附件").setInputFiles({
    name: "人员材料.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("新人一、新人二、新人三加入平台组"),
  })
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("帮我按材料导入名单")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.getByRole("form", { name: "上传名单" })).toHaveCount(0)
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toContainText("新增 3 人")
  await expect(card).toContainText("离职 0 人")
  await page.reload()
  await expect(card).toContainText("新增 3 人")
  const history = async () =>
    (await (await request.get(`${base}/roster/imports`, { headers })).json())
      .data
  expect(await history()).toHaveLength(1)
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  expect(await history()).toHaveLength(1)
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(await history()).toHaveLength(2)
  const accounts = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  expect(
    accounts.data.filter(
      (e: { employment_status: string }) => e.employment_status === "active",
    ),
  ).toHaveLength(4)
  await expect(
    card.getByRole("button", { name: "撤销", exact: true }),
  ).toHaveCount(0)
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("roster-chat-applied.png"),
    fullPage: true,
  })
})

test("preview work records in chat, repair errors and confirm", async ({
  page,
  request,
  modelUrl,
}, testInfo) => {
  const { email, password } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page.setViewportSize({ width: 390, height: 844 })
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("导入工作记录")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const input = page.getByLabel("选择工作记录")
  const csv =
    "记录编号,来源,发生时间,执行人账号,标题\n合成记录,合成系统,2026-01-01T09:00:00+08:00,合成账号,合成工作\n"
  await input.setInputFiles({
    name: "工作记录.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv.replace("合成账号", "")),
  })
  await page.getByRole("button", { name: "上传并预览" }).click()
  await expect(
    page.getByRole("region", { name: "文件预览" }).getByRole("alert"),
  ).toContainText("不能为空")
  await expect(
    page.getByRole("region", { name: "设置改动确认卡" }),
  ).toHaveCount(0)
  await input.setInputFiles({
    name: "工作记录.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  })
  await page.getByRole("button", { name: "上传并预览" }).click()
  const preview = page.getByRole("region", { name: "文件预览" }).last()
  await expect(preview).toContainText("记录条数")
  await expect(preview).toContainText("导入状态")
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card.getByRole("status")).toHaveText("待确认")
  await page.reload()
  await expect(preview).toContainText("涉及人数")
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  await expectChineseCopy(page)
  await page.screenshot({
    path: testInfo.outputPath("work-preview.png"),
    fullPage: true,
  })
})

test("dictionary materials produce a restored confirmation card and a new version", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page.getByLabel("选择对话附件").setInputFiles({
    name: "词典材料.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("合成能力：基础等级，在指导下完成。分类工程。"),
  })
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("按材料导入能力词典")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.getByRole("form", { name: "上传能力词典" })).toHaveCount(0)
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toContainText("新增 1 项")
  expect(
    (await request.get(`${base}/capabilities`, { headers })).status(),
  ).toBe(404)
  await page.reload()
  await expect(card).toContainText("新增 1 项")
  await card.getByRole("button", { name: "确认应用", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  const result = await request.get(`${base}/capabilities`, { headers })
  expect(result.ok()).toBeTruthy()
  const dictionary = await result.json()
  expect(dictionary.schema_version.version).toBe(1)
  expect(dictionary.data[0].name).toBe("合成能力")
})
