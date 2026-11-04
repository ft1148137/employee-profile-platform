import path from "node:path"
import type { ExternalIdentitiesPublic } from "../src/client"
import { configureModel, expect, test } from "./utils/model"
import { createSourcesOrg, saveCloud, syncJira } from "./utils/sources"

test.use({
  storageState: { cookies: [], origins: [] },
  trace: "off",
  screenshot: "off",
  video: "off",
})

test("M2 real Cloud: roster, manual identity, initial request and evidence", async ({
  page,
  modelUrl,
}) => {
  test.skip(
    !process.env.JIRA_BASE_URL ||
      !process.env.JIRA_EMAIL ||
      !process.env.JIRA_API_TOKEN,
    "未配置真实 Jira 测试凭据",
  )
  test.setTimeout(180000)
  await createSourcesOrg(page)
  await page.goto("/admin/roster")
  await page
    .getByLabel("拖拽名单到这里，或选择文件上传")
    .setInputFiles(
      path.resolve(import.meta.dirname, "../../examples/roster/sample-30.csv"),
    )
  await page.getByRole("button", { name: "确认导入", exact: true }).click()
  await expect(page.getByText("导入成功，员工名单和部门已更新。")).toBeVisible()
  await page.goto("/setup-wizard")
  const steps = page.getByRole("navigation", { name: "设置步骤" })
  await steps.getByRole("button", { name: /能力词典/ }).click()
  await page.getByRole("button", { name: /科技研发/ }).click()
  await page.getByRole("button", { name: "启用此词典" }).click()
  await expect(page.getByText("词典已启用，可以完成此步骤。")).toBeVisible()
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
  await page.goto("/admin/identities")
  await page.getByRole("button", { name: "选择员工" }).first().click()
  await page.getByRole("dialog").getByLabel("搜索员工姓名或工号").fill("XY002")
  await page.getByRole("dialog").getByRole("button", { name: /在职/ }).click()
  await expect(page.getByRole("status")).toContainText("已补充")
  const { base, headers } = await page.evaluate(() => ({
    base: `/api/v1/orgs/${localStorage.getItem("current_org_id")}`,
    headers: {
      Authorization: `Bearer ${localStorage.getItem("access_token")}`,
    },
  }))
  await page.goto("/setup-wizard")
  await steps.getByRole("button", { name: /配置模型/ }).click()
  await configureModel(page, modelUrl)
  await page.getByRole("button", { name: "完成此步骤并继续" }).click()
  await page.getByRole("button", { name: "生成初始画像", exact: true }).click()
  await expect(
    page.getByText(
      "已为 30 名员工生成初始画像，员工登录后能在自己的画像页看到",
    ),
  ).toBeVisible({ timeout: 60000 })
  const identities: ExternalIdentitiesPublic = await (
    await page.request.get(
      `${base}/external-identities?status=linked&limit=200`,
      { headers },
    )
  ).json()
  const employee = identities.data.find(
    (row) => row.employee?.employee_code === "XY002",
  )!.employee!
  const requests = await (
    await page.request.get(`${base}/employees/${employee.id}/change-requests`, {
      headers,
    })
  ).json()
  expect(requests.data).toHaveLength(1)
  const items = requests.data[0].items
  expect(items.length).toBeGreaterThan(0)
  const checked = new Set<string>()
  for (const item of items)
    for (const reference of item.evidence) {
      if (checked.has(reference.evidence_id)) continue
      checked.add(reference.evidence_id)
      const response = await page.request.get(
        `${base}/evidence/${reference.evidence_id}`,
        { headers },
      )
      expect(response.ok()).toBeTruthy()
      const evidence = await response.json()
      expect(evidence.title).toBe(reference.title)
      expect(evidence.status).toBe("active")
      const upstream = await page.request.get(
        `${process.env.JIRA_BASE_URL}/rest/api/3/issue/${evidence.source_ref}?fields=summary`,
        {
          headers: {
            Authorization: `Basic ${Buffer.from(`${process.env.JIRA_EMAIL}:${process.env.JIRA_API_TOKEN}`).toString("base64")}`,
          },
        },
      )
      expect(upstream.ok()).toBeTruthy()
      expect(evidence.title).toBe((await upstream.json()).fields.summary)
    }
  console.log(
    `真实 Cloud：改动项 ${items.length}，证据引用 ${items.reduce((n: number, i: { evidence: unknown[] }) => n + i.evidence.length, 0)}`,
  )
})
