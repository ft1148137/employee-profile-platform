import { expect, test } from "@playwright/test"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15_000 })

test("需求方创建多角色项目并看到承接方答复和排程影响", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const { open } = await setupProjectManagers(request)
  await open(page, "A", "/capacity?tab=projects")
  await page.getByRole("button", { name: "手工建项目" }).click()
  await page.getByLabel("项目名称").fill("合成跨部门交付")
  await page.getByLabel("需求方部门").selectOption({ label: "客户" })
  const addRole = page.getByRole("button", { name: "添加角色", exact: true })
  const roles = [
    ["技术验证", "测试", "2026-12-01", "2026-12-02"],
    ["接口开发", "研发", "2026-12-03", "2026-12-04"],
    ["上线验收", "测试", "2026-12-05", "2026-12-06"],
  ] as const
  for (const [index, [name, department, start, end]] of roles.entries()) {
    if (index) await addRole.click()
    const role = page.getByRole("group", {
      name: `角色 ${index + 1}`,
      exact: true,
    })
    await role.getByLabel("角色名称").fill(name)
    await role.getByLabel("目标部门").selectOption({ label: department })
    await role.getByLabel("主能力").selectOption({ label: "合成交付" })
    await role.getByLabel("人数").fill("1")
    await role.getByLabel("每人投入比例（%）").fill("50")
    await role.getByLabel("时间窗口开始").fill(start)
    await role.getByLabel("时间窗口结束").fill(end)
    await role.getByLabel("需求说明").fill(`交付要求：${name}`)
    if (index)
      await role.getByRole("checkbox", { name: roles[index - 1][0] }).check()
    if (index === 0) {
      await role.getByRole("button", { name: "添加额外能力" }).click()
      await role
        .getByRole("combobox", { name: "额外能力", exact: true })
        .selectOption({ label: "合成验证" })
      await role.getByRole("checkbox", { name: "必需（不勾选为可选）" }).check()
    }
  }

  await page.getByRole("button", { name: "提交项目需求" }).click()
  await expect(
    page.getByRole("heading", { name: "合成跨部门交付" }),
  ).toBeVisible()
  const projectPath = new URL(page.url()).pathname + new URL(page.url()).search
  await expect(page.getByRole("table", { name: "第 3 阶段" })).toContainText(
    "2026-12-05 至 2026-12-06",
  )
  const roleLink = page
    .getByRole("region", { name: "项目详情" })
    .getByRole("listitem")
    .filter({ hasText: "接口开发" })
    .getByRole("link", { name: "查看角色需求" })
  const outgoingPath = (await roleLink.getAttribute("href"))!
  await roleLink.click()
  const roleDetail = page.getByRole("region", { name: "需求详情" })
  await expect(
    roleDetail.getByRole("link", { name: "合成跨部门交付" }),
  ).toBeVisible()
  await expect(roleDetail).toContainText("角色 · 接口开发")

  await open(page, "B", "/capacity?tab=incoming")
  const incoming = page.locator("article").filter({ hasText: "角色：接口开发" })
  await incoming.getByRole("link", { name: "查看详情" }).click()
  await expect(page.getByRole("list", { name: "前置角色" })).toContainText(
    "技术验证（测试）",
  )
  await page.getByLabel("答复结论").selectOption({ label: "能做" })
  await page.getByLabel("最早开始日期").fill("2026-12-03")
  await page.getByLabel("承诺完成日期").fill("2026-12-06")
  await page.getByLabel("答复说明").fill("需要增加两个工作日完成开发")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "已答复",
  )
  await page.getByRole("link", { name: "合成跨部门交付" }).click()
  const receivingProject = page.getByRole("region", { name: "项目详情" })
  await expect(receivingProject).toContainText("接口开发 · 研发")
  await expect(receivingProject).not.toContainText("交付要求：技术验证")

  await open(page, "A", projectPath)
  const details = page.getByRole("region", { name: "项目详情" })
  await expect(
    details.getByRole("listitem").filter({ hasText: "接口开发" }),
  ).toContainText("已答复 · 能做")
  await expect(details).not.toContainText("合成研发经理")
  await expect(page.getByRole("table", { name: "第 3 阶段" })).toContainText(
    "2026-12-07 至 2026-12-08",
  )
  await expect(page.getByRole("region", { name: "项目影响" })).toContainText(
    "预计比需求窗口晚 2 天",
  )

  await open(page, "A", outgoingPath)
  await page.getByLabel("追问内容").fill("请重新确认承接日期")
  await page.getByRole("button", { name: "发送追问", exact: true }).click()
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "待答复",
  )
  await open(page, "B", "/capacity?tab=incoming")
  await incoming.getByRole("link", { name: "查看详情" }).click()
  await page.getByLabel("答复结论").selectOption("not_now")
  await page.getByLabel("拒绝原因").selectOption("scope")
  await page.getByLabel("答复说明").fill("需重新确认交付范围")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("region", { name: "需求详情" })).toContainText(
    "已答复",
  )
  await open(page, "A", projectPath)
  await expect(
    page.getByRole("region", { name: "分阶段时间表" }),
  ).toContainText("预计完成 待确认")
  const thirdStage = page.getByRole("table", { name: "第 3 阶段" })
  await expect(thirdStage).toContainText("2026-12-05 至 2026-12-06")
  await expect(thirdStage).toContainText("待确认")
  await expect(page.getByRole("region", { name: "项目影响" })).toContainText(
    "前置角色暂未给出可承接日期，项目预计完工日待确认。",
  )
})
