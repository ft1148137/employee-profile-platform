import { expect, test } from "@playwright/test"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] }, actionTimeout: 15_000 })

test("需求方创建多角色项目并看到承接方答复和排程影响", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000)
  const { open } = await setupProjectManagers(request)
  const planning = page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "规划", exact: true })
  const canvas = page.getByRole("main")
  const topbar = page.getByRole("banner")
  const manualDemand = topbar.getByRole("button", { name: "新建需求" })
  const manualProject = topbar.getByRole("button", { name: "新建项目" })

  await open(page, "C", "/inbox")
  await planning.click()
  await expect(
    canvas.getByRole("heading", { name: "我的负荷", exact: true }),
  ).toBeVisible()
  await expect(manualDemand).toHaveCount(0)
  await expect(manualProject).toHaveCount(0)

  await open(page, "A", "/inbox")
  await planning.click()
  await expect(
    page.getByRole("navigation", { name: "空间导航" }).getByRole("link"),
  ).toHaveText(["我的负荷", "团队负荷", "借调请求"])
  await page
    .getByRole("navigation", { name: "空间导航" })
    .getByRole("link", { name: "团队负荷", exact: true })
    .click()
  await expect(
    canvas.getByRole("heading", { name: "团队负荷", exact: true }),
  ).toBeVisible()
  await manualDemand.click()
  const demandDrawer = page.getByRole("dialog", { name: "新建协作需求" })
  await expect(demandDrawer.getByLabel("目标部门")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(demandDrawer).toHaveCount(0)
  await expect(manualDemand).toBeFocused()
  await manualDemand.click()
  await demandDrawer.getByLabel("需求方部门").selectOption({ label: "客户" })
  await demandDrawer.getByLabel("目标部门").selectOption({ label: "研发" })
  await demandDrawer
    .getByLabel("能力", { exact: true })
    .selectOption("delivery")
  await demandDrawer.getByLabel("人数").fill("1")
  await demandDrawer.getByLabel("每人投入比例（%）").fill("50")
  await demandDrawer.getByLabel("开始日期").fill("2026-12-01")
  await demandDrawer.getByLabel("结束日期").fill("2026-12-06")
  await demandDrawer.getByLabel("需求说明").fill("团队负荷入口提交的需求")
  await demandDrawer.getByRole("button", { name: "预览需求" }).click()
  await demandDrawer.getByRole("button", { name: "确认提交需求" }).click()
  await expect(demandDrawer).toHaveCount(0)
  const teamDemand = page.getByRole("region", { name: "需求详情", exact: true })
  await expect(teamDemand).toContainText("团队负荷入口提交的需求")
  await expect(teamDemand.locator("dt:has-text('当前状态') + dd")).toHaveText(
    "待答复",
  )
  await manualProject.click()
  await expect(page.getByRole("dialog", { name: "新建项目需求" })).toBeVisible()
  await page.getByLabel("项目名称").fill("合成跨部门交付")
  await page.getByLabel("需求方部门").selectOption({ label: "客户" })
  const addRole = page.getByRole("button", { name: "添加角色", exact: true })
  await addRole.click()
  await page
    .getByRole("group", { name: "角色 1", exact: true })
    .getByRole("button", { name: "移除此角色" })
    .click()
  await expect
    .soft(page.getByText("在后续角色中勾选前置角色来划分阶段"))
    .toBeVisible()
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
    await role.getByLabel("开始日期").fill(start)
    await role.getByLabel("结束日期").fill(end)
    await role.getByLabel("需求说明").fill(`交付要求：${name}`)
    if (index)
      await role.getByRole("checkbox", { name: roles[index - 1][0] }).check()
    if (index < 2) {
      await role.getByRole("button", { name: "添加额外能力" }).click()
      await role
        .getByRole("combobox", { name: "额外能力", exact: true })
        .selectOption({ label: "合成验证" })
      await role
        .getByRole("group", { name: "额外能力 1" })
        .getByLabel("最低等级")
        .selectOption("routine")
      if (index === 0)
        await role
          .getByRole("checkbox", { name: "必需（不勾选为可选）" })
          .check()
    }
  }

  const [projectCreated] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/capacity/projects") &&
        response.request().method() === "POST",
    ),
    page.getByRole("button", { name: "提交项目需求" }).click(),
  ])
  expect(projectCreated.ok(), await projectCreated.text()).toBeTruthy()
  await expect(
    page.getByRole("heading", { name: "合成跨部门交付" }),
  ).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(
    page.getByRole("complementary", { name: "项目属性" }),
  ).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  )
  await page.setViewportSize({ width: 1280, height: 720 })
  const projectPath = new URL(page.url()).pathname + new URL(page.url()).search
  await expect(page.getByRole("table", { name: "第 3 阶段" })).toContainText(
    "2026-12-05 至 2026-12-06",
  )
  await expect(page.getByRole("table", { name: "第 1 阶段" })).toContainText(
    "技术验证",
  )
  await expect(page.getByRole("table", { name: "第 2 阶段" })).toContainText(
    "接口开发",
  )
  const roleLink = page
    .getByRole("region", { name: "项目详情" })
    .getByRole("listitem")
    .filter({ hasText: "接口开发" })
    .getByRole("link", { name: "查看角色需求" })
  const outgoingPath = (await roleLink.getAttribute("href"))!
  await roleLink.click()
  const roleDetail = page.getByRole("region", { name: "需求详情" })
  const currentStatus = roleDetail.locator("dt:has-text('当前状态') + dd")
  await expect(
    roleDetail.getByRole("link", { name: "合成跨部门交付" }),
  ).toBeVisible()
  await expect(roleDetail).toContainText("角色 · 接口开发")
  await expect.soft(roleDetail).toContainText("合成交付 · 必需")
  await expect.soft(roleDetail).toContainText("合成验证 · 可选 · 独立完成")

  await open(page, "B", "/capacity?tab=incoming")
  await expect(
    page.getByRole("region", { name: "协作需求", exact: true }),
  ).toContainText("团队负荷入口提交的需求")
  const incoming = page
    .getByRole("region", { name: "协作需求", exact: true })
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: / · 接口开发$/ }) })
  await incoming.getByRole("link", { name: "查看详情" }).click()
  await expect.soft(roleDetail).toContainText("合成验证 · 可选 · 独立完成")
  await expect(page.getByRole("list", { name: "前置角色" })).toContainText(
    "技术验证（测试）",
  )
  await page.getByLabel("答复结论").selectOption({ label: "能做" })
  await page.getByLabel("最早开始日期").fill("2026-12-03")
  await page.getByLabel("承诺完成日期").fill("2026-12-06")
  await page.getByLabel("答复说明").fill("需要增加两个工作日完成开发")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(currentStatus).toHaveText("已答复")
  await page.getByRole("link", { name: "合成跨部门交付" }).click()
  const receivingProject = page.getByRole("region", { name: "项目详情" })
  await expect(receivingProject).toContainText("接口开发 · 研发")
  await expect(receivingProject).not.toContainText("交付要求：技术验证")

  await open(page, "A", projectPath)
  const details = page.getByRole("region", { name: "项目详情" })
  await expect(
    details
      .getByRole("list", { name: "角色需求" })
      .getByRole("listitem")
      .filter({ hasText: "接口开发" }),
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
  await expect(currentStatus).toHaveText("待答复")
  await open(page, "B", "/capacity?tab=incoming")
  await incoming.getByRole("link", { name: "查看详情" }).click()
  await page.getByLabel("答复结论").selectOption("not_now")
  await page.getByLabel("拒绝原因").selectOption("scope")
  await page.getByLabel("答复说明").fill("需重新确认交付范围")
  await page.getByLabel("最早开始日期").fill("2026-12-03")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect
    .soft(page.getByRole("alert"))
    .toContainText("请同时填写开始和结束日期")
  await page.getByLabel("最早开始日期").clear()
  await page.getByLabel("答复说明").fill("当前员工超负荷")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(page.getByRole("alert")).toContainText("请勿填写负荷描述")
  await expect(currentStatus).toHaveText("待答复")
  await page.getByLabel("答复说明").fill("需重新确认交付范围")
  await page.getByRole("button", { name: "确认发送答复" }).click()
  await expect(currentStatus).toHaveText("已答复")
  await open(page, "A", projectPath)
  await expect(
    page.getByRole("complementary", { name: "项目属性" }),
  ).toContainText("预计完成 待确认")
  const thirdStage = page.getByRole("table", { name: "第 3 阶段" })
  await expect(thirdStage).toContainText("2026-12-05 至 2026-12-06")
  await expect(thirdStage).toContainText("待确认")
  await expect(page.getByRole("region", { name: "项目影响" })).toContainText(
    "前置角色暂未给出可承接日期，项目预计完工日待确认。",
  )
})
