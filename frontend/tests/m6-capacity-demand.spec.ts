import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("无模型时需求表单完成跨部门承诺且 A 方只见汇总", async ({
  page,
  request,
}) => {
  test.setTimeout(120000)
  page.setDefaultTimeout(10000)
  const password = "synthetic-demand-password-2026"
  const post = async (url: string, data: object, token?: string) => {
    const response = await request.post(url, {
      data,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const setup = await post("/api/v1/private/setup-tokens", {
    purpose: "new_org",
  })
  const admin = await post("/api/v1/setup/initialize", {
    token: setup.token,
    org_name: "合成需求企业",
    admin_username: `demands-${crypto.randomUUID()}@example.com`,
    admin_password: password,
  })
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const people = [
    ["A", "客户员工", "客户", ""],
    ["M", "合成答复经理", "研发", ""],
    ["B1", "研发甲合成", "研发", "M"],
    ["B2", "研发乙合成", "研发", "M"],
    ["B3", "研发丙合成", "研发", "M"],
    ["B4", "研发丁合成", "研发", "M"],
  ]
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "roster.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,上级工号,邮箱\n${people.map((row) => `${row.join(",")},${crypto.randomUUID()}@example.com`).join("\n")}\n`,
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  await post(
    `${base}/roster/imports/${preview.id}/apply`,
    { base_revision: preview.base_revision },
    admin.access_token,
  )
  await post(
    `${base}/capabilities/versions`,
    {
      expected_version: 0,
      definitions: [
        {
          key: "delivery",
          name: "合成交付能力",
          category: "工程",
          description: "合成测试",
          level_rubric: {
            levels: [
              { key: "routine", label: "独立完成", description: "能独立完成" },
              {
                key: "advanced",
                label: "复杂任务",
                description: "能处理复杂任务",
              },
            ],
          },
        },
      ],
    },
    admin.access_token,
  )
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const employee = accounts.find((row) => row.employee_code === "A")!
  const manager = accounts.find((row) => row.employee_code === "M")!
  await post(
    `${base}/role-grants`,
    {
      user_id: manager.account!.user_id,
      role: "dept_manager",
      department_id: manager.department!.id,
    },
    admin.access_token,
  )
  const tokens = new Map<string, string>()
  for (const account of [employee, manager]) {
    const reset = await post(
      `${base}/accounts/${account.account!.user_id}/reset-password`,
      {},
      admin.access_token,
    )
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: reset.username, password: reset.initial_password },
    })
    expect(login.ok()).toBeTruthy()
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
      data: {
        current_password: reset.initial_password,
        new_password: password,
      },
    })
    expect(changed.ok()).toBeTruthy()
    tokens.set(account.employee_code!, (await changed.json()).access_token)
  }
  for (const person of accounts.filter((row) =>
    row.employee_code?.startsWith("B"),
  )) {
    await post(
      `${base}/employees/${person.id}/profile/direct-edits`,
      {
        base_revision: null,
        items: [
          {
            op: "add",
            capability_key: "delivery",
            statement: "合成工作经历",
            level: "routine",
          },
        ],
      },
      admin.access_token,
    )
  }
  const settings = async (requester_policy: "everyone" | "managers") => {
    const response = await request.put(`${base}/capacity-settings`, {
      headers,
      data: { enabled: true, requester_policy },
    })
    expect(response.ok()).toBeTruthy()
  }
  await settings("everyone")
  for (const [code, base_percent] of [
    ["B1", 50],
    ["B2", 90],
    ["B3", 110],
  ] as const) {
    const person = accounts.find((row) => row.employee_code === code)!
    expect(
      (
        await request.put(`${base}/capacity/weeks`, {
          headers: { Authorization: `Bearer ${tokens.get("M")}` },
          data: {
            items: [{ employee_id: person.id, other_percent: base_percent }],
            start_week: (
              await (
                await request.get(`${base}/capacity/workloads/me`, {
                  headers: { Authorization: `Bearer ${tokens.get("M")}` },
                })
              ).json()
            ).weeks[0].week_start,
            end_week: (
              await (
                await request.get(`${base}/capacity/workloads/me`, {
                  headers: { Authorization: `Bearer ${tokens.get("M")}` },
                })
              ).json()
            ).weeks[0].week_start,
          },
        })
      ).ok(),
    ).toBeTruthy()
  }
  const open = async (token: string, tab: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, orgId }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", orgId)
      },
      { token, orgId: admin.org_id },
    )
    await page.goto(`/capacity?tab=${tab}`)
    await expect(
      page.getByRole("heading", {
        name:
          tab === "incoming"
            ? "待审批"
            : tab === "team"
              ? "团队负荷"
              : "协作需求",
        exact: true,
      }),
    ).toBeVisible()
    await expect(page.getByRole("status", { name: "正在加载" })).toHaveCount(0)
  }
  const privateNames = people
    .filter(([code]) => code.startsWith("B"))
    .map(([, name]) => name)
  const privatePage = async () => {
    for (const name of privateNames)
      await expect(page.locator("body")).not.toContainText(name)
    await expect(
      page.getByRole("region", { name: "本部门匹配人员" }),
    ).toHaveCount(0)
  }
  await open(tokens.get("A")!, "outgoing")
  await privatePage()
  await expect(
    page.getByRole("link", { name: "协作需求", exact: true }),
  ).toHaveCount(0)
  await page.getByRole("button", { name: "新建需求" }).click()
  await page
    .getByLabel("需求方部门", { exact: true })
    .selectOption(employee.department!.id)
  await page
    .getByLabel("目标部门", { exact: true })
    .selectOption(manager.department!.id)
  await page.getByLabel("能力", { exact: true }).selectOption("delivery")
  await page.getByLabel("最低等级").selectOption("routine")
  const reference = page.getByRole("region", { name: "即时参考" })
  await expect(reference).toContainText(
    "达到要求的有 4 人；各人负荷只有对方部门经理能看到",
  )
  await expect(reference).not.toContainText(/有余量|基本满|超负荷|未确认/)
  await expect(reference).toContainText(
    "仅供参考，能否承接以目标部门经理正式答复为准。",
  )
  await page.getByLabel("最低等级").selectOption("advanced")
  await expect(reference).toContainText("达到要求的有 0 人")
  await page.getByLabel("最低等级").selectOption("routine")
  await privatePage()
  await page.getByLabel("人数", { exact: true }).fill("2")
  await page.getByLabel("每人投入比例（%）").fill("50")
  await page.getByLabel("开始日期").fill("2026-12-01")
  await page.getByLabel("结束日期").fill("2026-12-31")
  await page.getByLabel("需求说明").fill("合成需求：接口联调")
  await page.getByRole("button", { name: "预览需求" }).click()
  const demandPreview = page.getByRole("region", { name: "需求预览" })
  await expect(demandPreview).toContainText("客户 → 研发")
  await expect(demandPreview).toContainText("独立完成")
  await privatePage()
  await page.getByRole("button", { name: "返回修改" }).click()
  await expect(demandPreview).toHaveCount(0)
  await page.getByRole("button", { name: "预览需求" }).click()
  await page.getByRole("button", { name: "确认提交需求" }).click()
  const detail = page.getByRole("region", { name: "需求详情", exact: true })
  await expect(detail).toContainText("待答复")
  await expect(page.getByRole("button", { name: "接受答复" })).toHaveCount(0)
  await privatePage()
  const demandId = new URL(page.url()).searchParams.get("demand")
  expect(demandId).toBeTruthy()
  await page.getByRole("button", { name: "返回需求入口" }).click()
  await expect(page.getByRole("link", { name: "去 AI 助手" })).toBeVisible()
  const boardItem = page
    .getByRole("region", { name: "待答复", exact: true })
    .getByRole("article")
    .filter({ hasText: "合成需求：接口联调" })
  const requestNumber = Number.parseInt(demandId!.slice(-8), 16)
    .toString()
    .padStart(10, "0")
  await expect(
    boardItem.getByText(requestNumber, { exact: true }),
  ).toBeVisible()
  await expect(boardItem).toContainText("2 人 · 50%")
  const listView = page.getByRole("button", { name: "紧凑列表" })
  await listView.click()
  await expect(listView).toHaveAttribute("aria-pressed", "true")
  await expect(boardItem).toHaveCount(0)
  await page
    .getByRole("article")
    .filter({ hasText: "合成需求：接口联调" })
    .getByRole("link", { name: "查看详情" })
    .click()
  await expect(page).toHaveURL(`/capacity?tab=outgoing&demand=${demandId}`)
  await expect(detail).toContainText("合成需求：接口联调")
  await privatePage()
  for (const [index, conclusion] of ["暂时不能", "能做一部分"].entries()) {
    await open(tokens.get("M")!, "incoming")
    await page
      .getByRole("article")
      .filter({ hasText: "合成需求：接口联调" })
      .getByRole("link", { name: "查看详情", exact: true })
      .click()
    const basis = page.getByRole("region", { name: "本部门匹配人员" })
    for (const name of privateNames) await expect(basis).toContainText(name)
    await expect(basis).toContainText("独立完成")
    await expect(basis).toContainText("50%")
    await expect(basis).toContainText("基本满")
    if (!index)
      await page.screenshot({
        path: test.info().outputPath("demand-basis.png"),
        fullPage: true,
      })
    if (index)
      await expect(
        page.getByRole("region", { name: "事件时间线" }),
      ).toContainText("能否分阶段承接？")
    await page.getByLabel("答复结论").selectOption({ label: conclusion })
    if (!index) await page.getByLabel("拒绝原因").selectOption("capacity")
    await page.getByLabel("最早开始日期").fill("2026-12-05")
    await page.getByLabel("承诺完成日期").fill("2026-12-25")
    if (!index) {
      await page.getByLabel("答复说明").fill(`由${privateNames[0]}完成`)
      await page.getByRole("button", { name: "确认发送答复" }).click()
      await expect(page.getByRole("alert")).toContainText(
        "请勿填写本部门员工姓名",
      )
    }
    await page
      .getByLabel("答复说明")
      .fill(index ? "可以分阶段承接" : "当前容量不足")
    await page.getByRole("button", { name: "确认发送答复" }).click()
    await expect(detail).toContainText("已答复")
    await open(tokens.get("A")!, `outgoing&demand=${demandId}`)
    await expect(detail).toContainText("合成需求：接口联调")
    await privatePage()
    const timeline = page.getByRole("region", { name: "事件时间线" })
    await expect(timeline).toContainText(`结论：${conclusion}`)
    await expect(timeline).toContainText("2026-12-05")
    await expect(timeline).toContainText("2026-12-25")
    // Requesters must not see the responding department's personal names.
    await expect(timeline).toContainText("答复人：对方部门经理")
    await expect(page.locator("body")).not.toContainText("合成答复经理")
    await expect(timeline).toContainText("答复时人员概况")
    await privatePage()
    if (!index) {
      await expect(page.getByRole("button", { name: "接受答复" })).toHaveCount(
        0,
      )
      await expect(
        page.getByRole("button", { name: "发送追问" }),
      ).toBeDisabled()
      await page.getByLabel("追问内容").fill("能否分阶段承接？")
      await page.getByRole("button", { name: "发送追问" }).click()
      await expect(detail).toContainText("待答复")
    } else {
      await page.getByRole("button", { name: "接受答复" }).click()
      await expect(detail).toContainText("已接受")
    }
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: test.info().outputPath("demand-mobile.png"),
    fullPage: true,
  })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  const withdrawable = await post(
    `${base}/capacity/demands`,
    {
      requesting_department_id: employee.department!.id,
      target_department_id: manager.department!.id,
      capability_key: "delivery",
      headcount: 1,
      allocation_percent: 50,
      window_start: "2026-12-01",
      window_end: "2026-12-31",
      description: "合成待撤回需求",
    },
    tokens.get("A"),
  )
  await open(tokens.get("A")!, `outgoing&demand=${withdrawable.id}`)
  await expect(detail).toContainText("合成待撤回需求")
  await page.getByRole("button", { name: "撤回需求" }).click()
  await expect(detail).toContainText("已撤回")
  await expect(page.getByRole("button", { name: "撤回需求" })).toHaveCount(0)
  await privatePage()
  await settings("managers")
  await open(tokens.get("A")!, "outgoing")
  await expect(page.getByRole("button", { name: "新建需求" })).toHaveCount(0)
  for (const token of [admin.access_token, tokens.get("M")!]) {
    await open(token, "outgoing")
    await expect(page.getByRole("button", { name: "新建需求" })).toBeVisible()
  }
})
