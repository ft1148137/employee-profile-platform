import { expect, type Page, test } from "@playwright/test"
import type { ProfileView } from "../src/client"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
const selfReport = "我独立完成了分页接口，冲突处理还需要同事复核。"
test.use({
  viewport: { width: 1440, height: 1080 },
  storageState: { cookies: [], origins: [] },
})

async function openE02(page: Page, email: string) {
  await logInUser(page, email, password)
  if (email === "employee.platform@demo.example.com") {
    await expect(page).toHaveURL("/assistant")
    await page.getByRole("link", { name: "我的画像", exact: true }).click()
  }
  if (email !== "employee.platform@demo.example.com") {
    await page.goto("/employees")
    await page.getByRole("combobox", { name: "部门筛选" }).click()
    await page.getByRole("option", { name: "平台研发部", exact: true }).click()
    await page.getByRole("row").filter({ hasText: "张伟" }).click()
  }
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
}

test("administrator reads capability, Jira evidence and earlier published snapshot", async ({
  page,
}) => {
  await openE02(page, "admin@demo.example.com")
  const api = page.getByRole("article", { name: "后端 API 开发", exact: true })
  await expect(api.getByText("已确认", { exact: true })).toBeVisible()
  await expect(api.getByText("审核状态", { exact: true })).toHaveCount(0)
  await expect(
    page.getByRole("article").filter({ hasText: "未知：暂无足够证据" }),
  ).toHaveCount(2)
  await expect(page.getByText("证据质量", { exact: true })).toHaveCount(0)
  await expect(page.getByText("本部门还没有设置对应能力")).toBeVisible()
  await expect(page.getByRole("link", { name: "去组织架构设置" })).toHaveCount(
    1,
  )
  await expect(
    api.getByText("能独立完成复杂任务", { exact: true }),
  ).toBeVisible()
  await api.locator('[data-slot="tooltip-trigger"]').hover()
  await expect(page.getByRole("tooltip")).toContainText("不是经过校准的概率")
  await page.keyboard.press("Escape")
  await expect(page.getByRole("tooltip")).toHaveCount(0)
  await page.screenshot({
    animations: "disabled",
    path: "test-results/profile-capabilities.png",
    fullPage: true,
  })
  await api.locator("summary").click()
  await api.getByRole("button", { name: /实现分页查询接口/ }).click()
  await expect(page.getByRole("dialog")).toContainText("PLAT-104")
  await expect(page.getByRole("dialog")).toContainText("背景")
  await page.getByRole("button", { name: "关闭", exact: true }).click()
  await page.getByRole("tab", { name: "版本历史" }).click()
  await expect(
    page
      .getByRole("tabpanel", { name: "版本历史", exact: true })
      .getByRole("listitem"),
  ).toHaveCount(2)
  await page.getByRole("button", { name: /^r1 ·/ }).click()
  const snapshot = page.getByRole("region", { name: "r1 只读快照" })
  await expect(
    snapshot
      .getByRole("article", { name: "后端 API 开发", exact: true })
      .getByText("暂定", { exact: true }),
  ).toBeVisible()
  await page.screenshot({
    animations: "disabled",
    path: "test-results/profile-versions.png",
    fullPage: true,
  })
  await page.getByRole("tab", { name: "能力画像" }).click()
  await expect(api.getByText("已确认", { exact: true })).toBeVisible()
})

for (const [role, email, restricted] of [
  ["admin", "admin@demo.example.com", true],
  ["manager", "manager.platform@demo.example.com", false],
  ["employee", "employee.platform@demo.example.com", false],
] as const) {
  test(`${role} sees the permitted self-report content`, async ({ page }) => {
    await openE02(page, email)
    if (role !== "admin") {
      const api = page.getByRole("article", {
        name: "后端 API 开发",
        exact: true,
      })
      await expect(api).toContainText("能独立完成复杂任务")
      await expect(api).toContainText("已确认")
      for (const name of ["证据", "版本历史", "档案"])
        await expect(page.getByRole("tab", { name, exact: true })).toHaveCount(
          0,
        )
      for (const text of [
        "相关证据",
        "信心",
        "生成来源",
        "暂无解释",
        "证据较旧",
        selfReport,
      ])
        await expect(page.getByText(text, { exact: false })).toHaveCount(0)
      await expect(
        page.getByRole("button", { name: "导入 AI 生成的画像" }),
      ).toHaveCount(0)
      await expect(
        page.getByRole("region", { name: "员工档案摘要" }),
      ).toHaveCount(0)
      await expect(page.getByRole("switch")).toHaveCount(0)
      await page.screenshot({
        path: `test-results/profile-compact-${role}.png`,
        fullPage: true,
      })
      return
    }
    await page.getByRole("tab", { name: "证据", exact: true }).click()
    await page.getByLabel("证据类型").selectOption("self_report")
    await expect(
      page
        .getByRole("tabpanel", { name: "证据", exact: true })
        .getByRole("listitem"),
    ).toHaveCount(1)
    await page
      .getByRole("tabpanel", { name: "证据", exact: true })
      .getByRole("button")
      .click()
    const sheet = page.getByRole("dialog")
    await expect(sheet).toContainText("SR02")
    await expect(sheet.getByRole("listitem")).toHaveCount(1)
    await expect(sheet.getByRole("listitem")).toHaveText("后端 API 开发 · 支持")
    if (restricted) {
      await expect(sheet).toContainText(
        "受限内容：仅本人和管辖该部门的经理可见",
      )
      await expect(page.getByText(selfReport)).toHaveCount(0)
    } else await expect(sheet).toContainText(selfReport)
    await page.screenshot({
      animations: "disabled",
      path: `test-results/profile-evidence-${role}.png`,
      fullPage: true,
    })
  })
}

test("profile recovers from errors", async ({ page }) => {
  await page.route("**/employees/*/profile", (route) =>
    route.fulfill({ status: 500, json: { detail: "Temporary failure" } }),
  )
  await logInUser(page, "employee.platform@demo.example.com", password)
  await expect(page).toHaveURL("/assistant")
  await page.getByRole("link", { name: "我的画像", exact: true }).click()
  await expect(page.getByRole("alert")).toBeVisible()
  await page.unroute("**/employees/*/profile")
  await page.getByRole("button", { name: "重试" }).click()
  await expect(
    page.getByRole("heading", { name: "张伟", exact: true }),
  ).toBeVisible()
})

test("administrator tolerates incomplete evidence fields", async ({ page }) => {
  await openE02(page, "admin@demo.example.com")
  await page.route("**/evidence/*", async (route) => {
    const response = await route.fetch()
    const evidence = await response.json()
    await route.fulfill({
      json: {
        ...evidence,
        content: { text: 42, related_task_keys: null },
        kind: "self_report",
      },
    })
  })
  await page.getByRole("tab", { name: "证据", exact: true }).click()
  await page.getByLabel("证据类型").selectOption("work_record")
  await page
    .getByRole("tabpanel", { name: "证据", exact: true })
    .getByRole("button")
    .first()
    .click()
  await expect(page.getByRole("dialog")).toContainText("关联任务：—")
  await expect(page.getByRole("dialog")).toContainText("来源与导入信息")
})

for (const [email, title] of [
  ["employee.platform@demo.example.com", "待你过目：系统起草的初始画像"],
  ["manager.platform@demo.example.com", "待本人过目：系统起草的初始画像"],
  ["admin@demo.example.com", "待本人过目：系统起草的初始画像"],
]) {
  test(`${email} reads initial draft and opens its evidence`, async ({
    page,
  }) => {
    await openE02(page, email)
    const draft = page.getByRole("region", {
      name: "初始画像草稿",
      exact: true,
    })
    await expect(draft.getByRole("heading", { name: title })).toBeVisible()
    await expect(draft).toContainText("草稿，还没有生效。本人可以直接提交")
    await expect(draft.getByRole("region")).toHaveCount(2)
    if (email !== "employee.platform@demo.example.com") {
      await expect(
        draft.getByRole("button", {
          name: /^(修改|填写|提异议|直接提交|添加能力|撤回这条判断)$/,
        }),
      ).toHaveCount(0)
    }
    await expect(
      draft.getByText(
        email === "admin@demo.example.com"
          ? "等级：未给等级"
          : "→ 修改后：未给等级",
        { exact: true },
      ),
    ).toHaveCount(2)
    await expect(draft.getByText("暂定", { exact: true })).toHaveCount(2)
    await expect(
      draft.getByText(/不确定之处：任务关联不等于已独立完成/),
    ).toHaveCount(email === "admin@demo.example.com" ? 2 : 0)
    for (const capability of ["自动化测试", "问题诊断与支持"]) {
      await expect(
        page.getByRole("article", { name: capability, exact: true }),
      ).toContainText("有待处理的改动")
    }
    if (email === "admin@demo.example.com") {
      await draft
        .getByRole("button", { name: /实现分页查询接口/ })
        .first()
        .click()
      await expect(page.getByRole("dialog")).toContainText("PLAT-104")
    } else {
      for (const text of ["依据说明", "不确定之处", "请求时间线", "相关证据"])
        await expect(draft.getByText(text, { exact: false })).toHaveCount(0)
      await expect(
        draft.getByText("修改前：暂无判断", { exact: true }),
      ).toHaveCount(2)
    }
    await page.screenshot({
      animations: "disabled",
      path: `test-results/initial-draft-${email.split("@")[0]}.png`,
      fullPage: true,
    })
  })
}

test("another employee cannot read the initial draft", async ({ page }) => {
  await openE02(page, "employee.platform@demo.example.com")
  const url = page.url()
  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "退出登录" }).click()
  await logInUser(page, "employee.algorithm@demo.example.com", password)
  await page.goto(url)
  await expect(page.getByRole("alert")).toContainText(
    "未找到该员工或你没有权限查看",
  )
  await expect(
    page.getByRole("region", { name: "初始画像草稿", exact: true }),
  ).toHaveCount(0)
})

test("department capabilities filter counts, retain other claims and add through review", async ({
  page,
  request,
}) => {
  let profile: ProfileView
  await page.route("**/employees/*/profile", async (route) => {
    const response = await route.fetch()
    profile = await response.json()
    await route.fulfill({
      json: {
        ...profile,
        department_capabilities_configured: true,
        items: profile.items.map((item) => ({
          ...item,
          in_department: ["frontend_ui", "model_training"].includes(
            item.capability.key,
          ),
        })),
      },
    })
  })
  await page.route(
    "**/employees/*/change-requests?status=all",
    async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      const data = body.data.filter(
        (entry: { origin: string }) => entry.origin !== "initial_sync",
      )
      await route.fulfill({ json: { ...body, data, count: data.length } })
    },
  )
  await openE02(page, "employee.platform@demo.example.com")
  const known = page.getByRole("article", {
    name: "后端 API 开发",
    exact: true,
  })
  await expect(known).toContainText("其他部门能力")
  await expect(page.getByRole("article")).toHaveCount(3)
  const stats = page.getByRole("region", { name: "能力状态统计" })
  await expect(stats).toContainText("已确认 1")
  await expect(stats).toContainText("未知 2")
  await page.screenshot({
    path: "../docs/screenshots/m4.2/profile-department-capabilities.png",
    fullPage: true,
  })
  const hidden = profile!.items.find(
    (item) =>
      item.status === "unknown" &&
      ![
        "frontend_ui",
        "model_training",
        "automated_testing",
        "incident_support",
      ].includes(item.capability.key),
  )!
  await page.getByRole("button", { name: "添加能力", exact: true }).click()
  let dialog = page.getByRole("dialog")
  await dialog.getByLabel("搜索能力").fill(hidden.capability.name)
  await expect(
    dialog.getByRole("button", { name: hidden.capability.name, exact: true }),
  ).toHaveCount(1)
  await dialog
    .getByRole("button", { name: hidden.capability.name, exact: true })
    .click()
  await page.getByRole("button", { name: "手动编辑", exact: true }).click()
  dialog = page.getByRole("dialog", { name: "更新我的画像" })
  await expect(
    dialog.getByRole("textbox", { name: "陈述", exact: true }),
  ).toHaveValue("")
  await dialog
    .getByRole("textbox", { name: "陈述", exact: true })
    .fill("我完成了合成项目的能力验证")
  await dialog.getByLabel("依据说明").fill("本人确认的合成经历")
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/me/requests") &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "预览并提交" }).click()
  const createdResponse = await created
  const savedRequest = await createdResponse.json()
  const requestPath = new URL(createdResponse.url()).pathname
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  try {
    dialog = page.getByRole("dialog")
    await dialog.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(dialog).toBeHidden()
    await page.reload()
    const added = page.getByRole("article", {
      name: hidden.capability.name,
      exact: true,
    })
    await expect(added).toContainText("有待处理的改动")
    await expect(page.getByRole("article")).toHaveCount(4)
  } finally {
    expect(
      (
        await request.post(`${requestPath}/${savedRequest.id}/withdraw`, {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).ok(),
    ).toBeTruthy()
  }
})

test("administrator gets the department setup link and can add for another employee", async ({
  page,
}) => {
  await openE02(page, "admin@demo.example.com")
  await expect(page.getByText("本部门还没有设置对应能力")).toBeVisible()
  await expect(
    page.getByRole("link", { name: "去组织架构设置" }),
  ).toHaveAttribute("href", "/org-chart")
  await expect(
    page.getByRole("button", { name: "添加能力", exact: true }),
  ).toBeVisible()
})

test("empty initial draft cannot submit and pending requests can be withdrawn", async ({
  page,
}) => {
  await page.route(
    "**/employees/*/change-requests?status=all",
    async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      const initial = body.data.find(
        (entry: { origin: string }) => entry.origin === "initial_sync",
      )
      await route.fulfill({
        json: {
          data: [
            { ...initial, items: [] },
            {
              ...initial,
              id: "other",
              origin: "manual",
              status: "pending_review",
            },
          ],
          count: 2,
        },
      })
    },
  )
  await openE02(page, "employee.platform@demo.example.com")
  await expect(
    page.getByRole("region", { name: "初始画像草稿", exact: true }),
  ).toContainText("系统没有找到能对应到能力词典的工作记录。")
  const others = page.getByRole("region", { name: "其他待处理请求" })
  await expect(others).toContainText("待经理审批 · 2 项改动")
  await expect(
    others.getByRole("button", { name: "撤回", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "直接提交", exact: true }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "补充初始画像", exact: true }).click()
  const editor = page.getByRole("dialog")
  await editor.getByLabel("新增能力").selectOption("automated_testing")
  await expect(
    editor.getByRole("textbox", { name: "陈述", exact: true }),
  ).toBeVisible()
  await expect(
    editor.getByRole("textbox", { name: "异议原因", exact: true }),
  ).toBeVisible()
})

test("initial draft read failure can be retried", async ({ page }) => {
  await page.route("**/employees/*/change-requests?status=all", (route) =>
    route.fulfill({ status: 500 }),
  )
  await openE02(page, "employee.platform@demo.example.com")
  await expect(page.getByRole("alert")).toBeVisible()
  await page.unroute("**/employees/*/change-requests?status=all")
  await page.getByRole("button", { name: "重试" }).click()
  await expect(
    page.getByRole("region", { name: "初始画像草稿", exact: true }),
  ).toBeVisible()
})

test("pending change shows levels and expandable statement while keeping basis hidden", async ({
  page,
}) => {
  await page.route(
    "**/employees/*/change-requests?status=all",
    async (route) => {
      const profileResponse = await route.fetch({
        url: route
          .request()
          .url()
          .replace(/change-requests\?status=all$/, "profile"),
      })
      const profile: ProfileView = await profileResponse.json()
      const existing = profile.items.find(
        (item) => item.capability.key === "backend_api",
      )!
      const response = await route.fetch()
      const body = await response.json()
      const initial = body.data.find(
        (entry: { origin: string }) => entry.origin === "initial_sync",
      )
      await route.fulfill({
        json: {
          data: [
            {
              ...initial,
              id: "pending-modify",
              origin: "manual",
              status: "pending_review",
              items: [
                {
                  ...initial.items[0],
                  capability: existing.capability,
                  target_claim_id: existing.claim!.id,
                  after: {
                    ...initial.items[0].after,
                    level: null,
                    statement: "本人提交的陈述\n".repeat(8),
                  },
                  owner_statement: "不应显示的依据",
                },
              ],
            },
          ],
          count: 1,
        },
      })
    },
  )
  await openE02(page, "employee.platform@demo.example.com")
  const pending = page.getByRole("region", { name: "其他待处理请求" })
  await expect(pending).toContainText("修改前：能独立完成复杂任务")
  await expect(pending).toContainText("修改后：未定级")
  await expect(pending).toContainText("已确认")
  await expect(pending).toContainText("暂定")
  const statement = pending.locator("p").filter({ hasText: "本人提交的陈述" })
  await expect(statement).toBeVisible()
  const collapsedHeight = (await statement.boundingBox())!.height
  await pending.getByRole("button", { name: "展开陈述" }).click()
  expect((await statement.boundingBox())!.height).toBeGreaterThan(
    collapsedHeight,
  )
  await pending.getByRole("button", { name: "收起陈述" }).click()
  expect((await statement.boundingBox())!.height).toBe(collapsedHeight)
  await expect(page.getByText("不应显示的依据")).toHaveCount(0)
  await expect(
    pending.getByRole("button", { name: "撤回", exact: true }),
  ).toBeVisible()
})

test("editing an initial candidate sends uncertainty back unchanged", async ({
  page,
}) => {
  await openE02(page, "employee.platform@demo.example.com")
  const initial = page.getByRole("region", {
    name: "初始画像草稿",
    exact: true,
  })
  await initial
    .getByRole("button", { name: "修改", exact: true })
    .first()
    .click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByRole("textbox", { name: "陈述", exact: true })
    .first()
    .fill("合成修改，不丢不确定说明")
  let uncertainties: (string | null)[] = []
  await page.route("**/me/requests/*/items", async (route) => {
    uncertainties = route
      .request()
      .postDataJSON()
      .items.map((item: { uncertainty: string | null }) => item.uncertainty)
    // Capture the UI boundary without modifying the shared demo candidate.
    await route.fulfill({ json: { id: "synthetic", revision: 2 } })
  })
  await dialog.getByRole("button", { name: "保存草稿", exact: true }).click()
  await expect(dialog.getByRole("status")).toHaveText("草稿已保存")
  expect(uncertainties).toHaveLength(2)
  expect(
    uncertainties.every((value) => value?.includes("任务关联不等于已独立完成")),
  ).toBe(true)
})
