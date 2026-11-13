import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import {
  createSourcesOrg,
  saveCloud,
  setupStep,
  syncJira,
} from "./utils/sources"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
const base = "http://mock-jira:8080/qiguang-aiot"

for (const deployment of ["cloud", "data_center"]) {
  test(`${deployment}: choose project scope before syncing`, async ({
    page,
  }) => {
    test.setTimeout(120000)
    await createSourcesOrg(page)
    if (deployment === "cloud") {
      await saveCloud(page, base, "jira-admin@example.com", "mock-cloud-token")
    } else {
      await page.getByLabel("Jira Data Center / Server").check()
      await page.getByLabel("站点地址").fill(base)
      await page.getByLabel("个人访问令牌", { exact: true }).fill("mock-dc-pat")
      await page.getByRole("button", { name: "保存", exact: true }).click()
      await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
    }
    await expect(
      page.getByText("将同步：全部项目", { exact: true }),
    ).toBeVisible()
    await page.getByRole("button", { name: "测试连接" }).click()
    await page
      .getByRole("checkbox", { name: "EDGE（EDGE）", exact: true })
      .check()
    await expect(
      page.getByText("将同步：EDGE（EDGE）", { exact: true }),
    ).toBeVisible()
    await expect(page.getByRole("button", { name: "立即同步" })).toBeDisabled()
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
    await page.reload()
    await expect(page.getByLabel("同步项目标识")).toHaveValue("EDGE")
    await syncJira(page)
    const status = page
      .getByRole("status")
      .filter({ hasText: "Jira 同步：成功" })
    await expect(status).toContainText("记录 20")
    await expect(status).toContainText("新增任务 20")
    const history = page.getByRole("region", { name: "同步记录", exact: true })
    await expect(history).toContainText("项目范围：EDGE")
    await page.getByLabel("同步项目标识").fill("fw,EDGE,fw")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByLabel("同步项目标识")).toHaveValue("FW,EDGE")
    await expect(history).toContainText("项目范围：EDGE")
    await expect(page.getByText(/缩小范围不会删除已经同步的记录/)).toBeVisible()
  })
}

test("Cloud connection, secret preservation, sync and complete source step", async ({
  page,
}, testInfo) => {
  test.setTimeout(120000)
  await createSourcesOrg(page)
  const blocked = await setupStep(page, "sources")
  expect(blocked.status()).toBe(422)
  expect((await blocked.json()).detail.blockers).toContain(
    "还没有成功同步或导入过工作记录",
  )
  await saveCloud(page, base, "jira-admin@example.com", "wrong-token")
  await page.getByRole("button", { name: "测试连接" }).click()
  await expect(
    page.getByRole("alert").filter({ hasText: "账号或令牌不正确" }),
  ).toBeVisible()
  await expect(
    page.getByText("如果暂时接不上，可以先在下方上传工作记录文件"),
  ).toBeVisible()
  await saveCloud(page, base, "jira-admin@example.com", "mock-cloud-token")
  await page.reload()
  await expect(page.getByLabel("API token", { exact: true })).toHaveAttribute(
    "placeholder",
    "已保存，留空表示不修改",
  )
  await expect(page.getByLabel("API token", { exact: true })).toHaveValue("")
  expect(await page.content()).not.toContain("mock-cloud-token")
  const response = await page.evaluate(async () => {
    const result = await fetch(
      `/api/v1/orgs/${localStorage.getItem("current_org_id")}/sources/jira`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("access_token")}`,
        },
      },
    )
    return result.text()
  })
  expect(response).not.toContain("mock-cloud-token")
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
  await page.getByRole("button", { name: "测试连接" }).click()
  await expect(page.getByText(/Jira 连接成功：.+/)).toBeVisible()
  await syncJira(page)
  await expect(
    page.getByRole("status").filter({ hasText: "Jira 同步：成功" }),
  ).toContainText("记录 36")
  await expectChineseCopy(page, [
    "Jira Admin",
    "EDGE", // Project identifiers come from Jira, not interface copy.
    "Cloud",
    "Data Center",
    "Server",
    "Atlassian",
    "token",
    "description",
    "PLM",
    "Git",
  ])
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({
    path: testInfo.outputPath("sources-synced.png"),
    fullPage: true,
  })
  const completed = await setupStep(page, "sources")
  expect(completed.ok()).toBeTruthy()
  expect(
    (await completed.json()).steps.find(
      (step: { key: string }) => step.key === "sources",
    ).status,
  ).toBe("completed")
  await page.goto("/admin/sources")
  await expect(
    page.getByRole("heading", { name: "数据来源", exact: true }),
  ).toBeVisible()
})

test("Data Center PAT, file errors, warnings and successful import", async ({
  page,
}) => {
  test.setTimeout(120000)
  await createSourcesOrg(page)
  await page.getByLabel("Jira Data Center / Server").check()
  await page.getByLabel("站点地址").fill(base)
  await page.getByLabel("个人访问令牌", { exact: true }).fill("mock-dc-pat")
  await page.getByRole("button", { name: "保存", exact: true }).click()
  await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
  await page.getByRole("button", { name: "测试连接" }).click()
  await expect(page.getByText(/Jira 连接成功：.+/)).toBeVisible()
  for (const format of ["Excel", "CSV"]) {
    const download = page.waitForEvent("download")
    await page.getByRole("button", { name: `下载 ${format} 模板` }).click()
    expect((await download).suggestedFilename()).toBe(
      `工作记录模板.${format === "Excel" ? "xlsx" : "csv"}`,
    )
  }
  const csv =
    "记录编号,来源,发生时间,执行人账号,标题\nSYN-1,合成系统,invalid,synthetic,合成记录\n"
  const upload = page.getByLabel("选择工作记录文件")
  await upload.setInputFiles({
    name: "synthetic.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  })
  const table = page.getByRole("table", { name: "文件错误和提醒" })
  await expect(
    table.getByRole("row").filter({ hasText: "时间无法解析" }),
  ).toContainText("2")
  await expect(
    page.getByText("文件有错误，整份文件没有导入，请修改后重新上传。"),
  ).toBeVisible()
  await upload.setInputFiles({
    name: "synthetic.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv.replace("invalid", "2026-10-01 09:00")),
  })
  await expect(page.getByText("已开始导入", { exact: true })).toBeVisible()
  await expect(table).toContainText("提醒")
  await expect(page.getByText("文件导入：成功", { exact: true })).toBeVisible({
    timeout: 90000,
  })
  await expect(
    page.getByRole("status").filter({ hasText: "文件导入：成功" }),
  ).toContainText("记录 1")
  await upload.setInputFiles({
    name: "large.csv",
    mimeType: "text/csv",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
  })
  await expect(page.getByRole("alert")).toContainText(
    "文件超过 5 MB，请拆分后再上传",
  )
})

for (const account of ["manager.platform", "employee.platform"]) {
  test(`${account}: sources menu and direct access are restricted`, async ({
    page,
  }) => {
    await logInUser(
      page,
      `${account}@demo.example.com`,
      process.env.DEMO_USER_PASSWORD!,
    )
    await expect(
      page.getByRole("link", { name: "数据与 AI", exact: true }),
    ).toHaveCount(0)
    await page.goto("/admin/sources")
    await expect(page.getByRole("alert")).toContainText(
      "你没有权限管理数据来源",
    )
    await expect(page.getByLabel("站点地址")).toHaveCount(0)
  })
}

test("Jira schedule persists after refresh and can be disabled", async ({
  page,
}) => {
  await createSourcesOrg(page)
  await saveCloud(page, base, "jira-admin@example.com", "mock-cloud-token")
  await page.goto("/admin/sources")
  await expect(page.getByLabel("同步周期")).toHaveValue("off")
  for (const period of ["monthly", "quarterly", "yearly", "off"]) {
    await page.getByLabel("同步周期").selectOption(period)
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByText("Jira 配置已保存。")).toBeVisible()
    await page.reload()
    await expect(page.getByLabel("同步周期")).toHaveValue(period)
    const next = page.getByText(/下次同步时间：/)
    await expect(next).toBeVisible()
    if (period === "off") await expect(next).toContainText("定时同步已关闭")
    else await expect(next).not.toContainText("定时同步已关闭")
    await expect(page.getByRole("button", { name: "立即同步" })).toBeEnabled()
  }
})
