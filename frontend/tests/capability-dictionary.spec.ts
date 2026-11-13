import { expect, test } from "@playwright/test"
import type { ProfileView } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin exports a dictionary and opens material import in the assistant", async ({
  page,
  request,
}) => {
  const email = `dictionary-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "词典合成测试企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expectChineseCopy(page, ["YAML", "AI"])
  await expect(page.getByRole("button", { name: "下载模板" })).toHaveCount(0)
  await expect(page.locator('input[type="file"]')).toHaveCount(0)
  const { access_token, org_id } = await initialized.json()
  const response = await request.put(`/api/v1/orgs/${org_id}/capabilities`, {
    headers: { Authorization: `Bearer ${access_token}` },
    data: {
      expected_version: 0,
      definitions: [
        {
          key: "custom",
          name: "合成能力",
          category: "工程",
          description: "合成说明",
          level_rubric: {
            levels: [
              { key: "basic", label: "基础", description: "在指导下完成" },
            ],
          },
        },
      ],
    },
  })
  expect(response.ok()).toBeTruthy()
  await page.reload()
  await expect(page.getByText("当前第 1 版 · 1 项能力")).toBeVisible()
  const exported = page.waitForEvent("download")
  await page.getByRole("button", { name: "导出当前词典" }).click()
  expect((await exported).suggestedFilename()).toBe("本企业能力词典.yaml")
  await page.getByRole("link", { name: "在管理助手中导入词典" }).click()
  await expect(page).toHaveURL(/\/admin\/assistant/)
  await expect(
    page.getByRole("textbox", { name: "发给管理助手的消息" }),
  ).toHaveValue("帮我按这份材料导入能力词典")
  await page.screenshot({
    path: "test-results/capability-dictionary.png",
    fullPage: true,
  })
})

for (const email of [
  "employee.platform@demo.example.com",
  "manager.platform@demo.example.com",
]) {
  test(`${email} can export but cannot edit`, async ({ page }) => {
    await logInUser(page, email, process.env.DEMO_USER_PASSWORD!)
    await page.goto("/admin/rules?tab=dictionary")
    await page.getByRole("tab", { name: "能力词典", exact: true }).click()
    await expect(
      page.getByRole("button", { name: "导出当前词典" }),
    ).toBeEnabled()
    await expect(
      page.getByRole("region", { name: "上传能力词典" }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "在网页上编辑" }),
    ).toHaveCount(0)
    await expectChineseCopy(page)
  })
}

test("web editor saves additions, levels and removals in a new enterprise at 375px", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000)
  await page.addInitScript(() => {
    Object.defineProperty(window.crypto, "randomUUID", { value: undefined })
  })
  const email = `dictionary-editor-${crypto.randomUUID()}@example.com`
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "网页词典合成企业",
      admin_username: email,
      admin_password: "synthetic-password-2026",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}`
  const definitions = ["retained", "removed"].map((key, index) => ({
    key,
    name: index ? "将删除的能力" : "保留的能力",
    category: "工程",
    description: "合成说明",
    level_rubric: {
      levels: [{ key: "basic", label: "基础", description: "旧等级说明" }],
    },
  }))
  expect(
    (
      await request.put(`${base}/capabilities`, {
        headers,
        data: { definitions, expected_version: 0 },
      })
    ).ok(),
  ).toBeTruthy()
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,工程部,,,synthetic@example.com\n",
        ),
      },
    },
  })
  const imported = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${imported.id}/apply`, {
        headers,
        data: { base_revision: imported.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  const employees = await request.get(`${base}/employees`, { headers })
  const employee = (await employees.json()).data[0]
  const employeeId = employee.id
  await logInUser(page, email, "synthetic-password-2026")
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  const editor = page.getByRole("region", { name: "网页编辑能力词典" })
  const retained = editor.getByRole("article", {
    name: "保留的能力",
    exact: true,
  })
  await expect(retained.getByLabel("能力标识")).toBeDisabled()
  await retained.getByLabel("等级描述").fill("可以独立完成合成任务")
  await editor
    .getByRole("article", { name: "将删除的能力" })
    .getByRole("button", { name: "删除能力" })
    .click()
  await editor.getByRole("button", { name: "新增能力", exact: true }).click()
  const added = editor.getByRole("article").last()
  await added.getByLabel("能力名称").fill("新合成能力")
  await added.getByLabel("能力说明").fill("用于验证未知状态")
  await added.getByLabel("等级描述").fill("能在指导下完成")
  await expect(
    added.getByRole("checkbox", { name: "不由 AI 推断" }),
  ).toHaveCount(0)
  await editor.getByRole("button", { name: "预览改动" }).click()
  const preview = editor.getByRole("region", { name: "词典改动预览" })
  for (const text of [
    "新增 1 项",
    "修改 1 项",
    "删除 1 项",
    "0 条判断保留",
    "0 条待提交改动项将移除",
  ])
    await expect(preview).toContainText(text)
  await expectChineseCopy(page, [
    "YAML",
    "AI",
    "retained",
    "removed",
    "capability",
  ])
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.screenshot({
    path: testInfo.outputPath("dictionary-editor-mobile.png"),
    fullPage: true,
  })
  await preview.getByRole("button", { name: "确认保存新版本" }).click()
  await expect(
    page.getByRole("status").filter({ hasText: "新词典已启用" }),
  ).toBeVisible()
  await page.reload()
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  await expect(page.getByText("当前第 2 版 · 2 项能力")).toBeVisible()
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  await expect(retained.getByLabel("等级描述")).toHaveValue(
    "可以独立完成合成任务",
  )
  await expect(
    editor.getByRole("article", { name: "将删除的能力" }),
  ).toHaveCount(0)
  await expect(
    editor.getByRole("article", { name: "新合成能力" }),
  ).toBeVisible()
  await page.goto("/admin/drafting")
  await expect(page).toHaveURL("/admin/data-ai?tab=model")
  await expect(
    page.getByRole("switch", { name: "自动起草", exact: true }),
  ).toBeVisible()
  await page.goto(`/employees/${employeeId}`)
  await expect(page.getByText("本部门还没有设置对应能力")).toBeVisible()
  await expect(
    page.getByRole("article", { name: "新合成能力", exact: true }),
  ).toHaveCount(0)
  const profile: ProfileView = await (
    await request.get(`${base}/employees/${employeeId}/profile`, { headers })
  ).json()
  const newCapability = profile.items.find(
    (item) => item.capability.name === "新合成能力",
  )!
  expect(newCapability.status).toBe("unknown")
  expect(
    (
      await request.put(
        `${base}/departments/${employee.department.id}/capabilities`,
        {
          headers,
          data: { capability_keys: [newCapability.capability.key] },
        },
      )
    ).ok(),
  ).toBeTruthy()
  await page.reload()
  await expect(
    page.getByRole("article", { name: "新合成能力", exact: true }),
  ).toContainText("未知")
})

test("admin browses categories, levels, published counts and searches", async ({
  page,
}) => {
  await logInUser(
    page,
    "admin@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  const browser = page.getByRole("region", { name: "浏览能力词典" })
  const first = browser.getByRole("article").first()
  await expect(
    first.getByRole("region", { name: "已发布判断统计" }),
  ).toBeVisible()
  await expect(first.getByRole("list", { name: "等级说明" })).toBeVisible()
  await expect(first.getByRole("list", { name: "等级分布" })).toContainText(
    "未定等级",
  )
  await expect(first.getByRole("list", { name: "判断状态分布" })).toContainText(
    "存在争议",
  )
  const name = (await first.getByRole("heading").textContent())?.trim() ?? ""
  expect(name).not.toBe("")
  await browser.getByLabel("搜索能力").fill(name)
  await expect(browser.getByRole("article")).toHaveCount(1)
  const category = browser.locator("summary").first()
  await category.click()
  await expect(first).toBeHidden()
  await category.click()
  await browser.getByLabel("搜索能力").fill("不存在的合成能力")
  await expect(browser.getByRole("article")).toHaveCount(0)
  await expect(browser.getByRole("status")).toContainText("没有匹配的能力")
  await browser.getByLabel("搜索能力").fill("")
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({
    path: "../docs/screenshots/m4.2/capability-browser-admin.png",
    fullPage: true,
  })
})

test("employee browses and searches without requesting statistics", async ({
  page,
}) => {
  const statsRequests: string[] = []
  page.on("request", (request) => {
    if (request.url().includes("/capabilities/stats"))
      statsRequests.push(request.url())
  })
  await logInUser(
    page,
    "employee.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  await page.goto("/admin/rules?tab=dictionary")
  await page.getByRole("tab", { name: "能力词典", exact: true }).click()
  const browser = page.getByRole("region", { name: "浏览能力词典" })
  const card = browser.getByRole("article").first()
  await expect(card).toBeVisible()
  const description = (await card.locator("header p").textContent()) ?? ""
  expect(description).not.toBe("")
  await browser.getByLabel("搜索能力").fill(description)
  await expect(browser.getByRole("article")).toHaveCount(1)
  await expect(
    browser.getByRole("region", { name: "已发布判断统计" }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "编辑词典", exact: true }),
  ).toHaveCount(0)
  expect(statsRequests).toEqual([])
  await page.goto("/admin/rules?tab=dictionary#dictionary-editor")
  await expect(browser.getByRole("article").first()).toBeVisible()
})

test("draft proposals enter the dictionary through preview and save", async ({
  page,
  request,
}) => {
  const email = `proposals-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "建议处理合成企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}/capabilities`
  expect(
    (
      await request.put(base, {
        headers,
        data: {
          expected_version: 0,
          definitions: [
            {
              key: "existing",
              name: "已有能力",
              category: "工程",
              description: "合成说明",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "旧等级说明",
                  },
                ],
              },
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  for (const name of ["新合成能力", "将忽略的能力"]) {
    expect(
      (
        await request.post(
          `/api/v1/private/capability-proposals?org_id=${org_id}&name=${encodeURIComponent(name)}`,
        )
      ).ok(),
    ).toBeTruthy()
  }
  await logInUser(page, email, password)
  await page.goto("/admin")
  await page
    .getByRole("listitem")
    .filter({ hasText: "2 个起草时提议的新能力" })
    .getByRole("link", { name: "去处理", exact: true })
    .click()
  const panel = page.getByRole("region", { name: "建议新增的能力" })
  page.once("dialog", (dialog) => dialog.accept())
  await panel
    .getByRole("article", { name: "建议：将忽略的能力" })
    .getByRole("button", { name: "忽略", exact: true })
    .click()
  await expect(
    panel.getByRole("article", { name: "建议：将忽略的能力" }),
  ).toHaveCount(0)
  await panel.getByRole("button", { name: "加入词典", exact: true }).click()
  const editor = page.getByRole("region", { name: "网页编辑能力词典" })
  const added = editor.getByRole("article", { name: "新合成能力", exact: true })
  await expect(added.getByLabel("能力标识")).toHaveValue("")
  await expect(added.getByLabel("分类", { exact: true })).toHaveValue("工程")
  await expect(added.getByLabel("能力说明")).toHaveValue("合成能力说明")
  await expect(added.getByLabel("等级标识")).toHaveValue("routine")
  await expect(added.getByLabel("等级描述")).toHaveValue("")
  await added.getByLabel("能力标识").fill("capability_1")
  await added.getByLabel("等级描述").fill("能独立完成合成任务")
  await editor.getByRole("button", { name: "预览改动", exact: true }).click()
  await editor
    .getByRole("region", { name: "词典改动预览" })
    .getByRole("button", { name: "确认保存新版本" })
    .click()
  await expect(
    page.getByRole("status").filter({ hasText: "新词典已启用" }),
  ).toBeVisible()
  const listed = await request.get(`${base}/proposals`, { headers })
  expect((await listed.json()).data).toEqual([])
  await page.getByRole("button", { name: "编辑词典", exact: true }).click()
  await expect(panel).toHaveCount(0)
  await expect(added).toBeVisible()
})

test("employee suggests a missing capability and can add it after admin acceptance", async ({
  page,
  request,
  browser,
}) => {
  const password = "synthetic-password-2026"
  const adminEmail = `suggestion-admin-${crypto.randomUUID()}@example.com`
  const employeeEmail = `suggestion-employee-${crypto.randomUUID()}@example.com`
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "员工建议合成企业",
      admin_username: adminEmail,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}`
  expect(
    (
      await request.put(`${base}/capabilities`, {
        headers,
        data: {
          expected_version: 0,
          definitions: [
            {
              key: "existing",
              name: "已有能力",
              category: "工程",
              description: "合成说明",
              level_rubric: {
                levels: [
                  {
                    key: "basic",
                    label: "基础",
                    description: "能在指导下完成",
                  },
                ],
              },
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\n001,合成建议员工,工程部,,,${employeeEmail}\n`,
        ),
      },
    },
  })
  const imported = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${imported.id}/apply`, {
        headers,
        data: { base_revision: imported.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const accounts = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  const employee = accounts.data[0]
  const reset = await request.post(
    `${base}/accounts/${employee.account.user_id}/reset-password`,
    { headers },
  )
  expect(reset.ok()).toBeTruthy()
  const initialPassword = (await reset.json()).initial_password
  await page.goto("/login")
  await page.getByTestId("email-input").fill(employeeEmail)
  await page.getByTestId("password-input").fill(initialPassword)
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page).toHaveURL("/change-password")
  await page.getByLabel("当前密码", { exact: true }).fill(initialPassword)
  await page.getByLabel("新密码", { exact: true }).fill(password)
  await page.getByLabel("确认新密码", { exact: true }).fill(password)
  await page.getByRole("button", { name: "保存密码", exact: true }).click()
  await expect(page).toHaveURL("/assistant")
  await page.goto(`/employees/${employee.id}`)
  await page.getByRole("button", { name: "添加能力", exact: true }).click()
  const picker = page.getByRole("dialog")
  await picker.getByLabel("搜索能力").fill("研发费用归集")
  await picker.getByRole("button", { name: "建议新增这项能力" }).click()
  await expect(picker.getByLabel("能力名称")).toHaveValue("研发费用归集")
  await picker.getByLabel("能力说明").fill("按研发项目归集费用")
  await picker.getByLabel("为什么需要").fill("我的工作需要这项能力")
  await picker.getByRole("button", { name: "提交建议" }).click()
  await expect(picker.getByRole("status")).toHaveText(
    "已提交给企业管理员，加进词典后就可以在这里添加",
  )
  await page.screenshot({ path: "test-results/capability-suggestion.png" })
  const adminContext = await browser.newContext({
    storageState: { cookies: [], origins: [] },
  })
  try {
    const admin = await adminContext.newPage()
    await logInUser(admin, adminEmail, password)
    await admin.goto("/admin/rules?tab=dictionary#dictionary-editor")
    const editor = admin.getByRole("region", { name: "网页编辑能力词典" })
    const proposal = editor.getByRole("article", { name: "建议：研发费用归集" })
    await expect(proposal).toContainText("员工建议：合成建议员工")
    await proposal.screenshot({
      path: "test-results/capability-suggestion-admin.png",
    })
    await proposal
      .getByRole("button", { name: "加入词典", exact: true })
      .click()
    const added = editor.getByRole("article", {
      name: "研发费用归集",
      exact: true,
    })
    await added.getByLabel("能力标识").fill("research_cost")
    await added.getByLabel("等级描述").fill("能按项目完成费用归集")
    await editor.getByRole("button", { name: "预览改动" }).click()
    await editor.getByRole("button", { name: "确认保存新版本" }).click()
    await expect(
      admin.getByRole("status").filter({ hasText: "新词典已启用" }),
    ).toBeVisible()
    await page.reload()
    await page.getByRole("button", { name: "添加能力", exact: true }).click()
    await picker.getByLabel("搜索能力").fill("研发费用归集")
    await picker
      .getByRole("button", { name: "研发费用归集", exact: true })
      .click()
    await expect(page.getByRole("dialog")).toContainText("研发费用归集")
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "手动编辑", exact: true })
      .click()
    await expect(
      page.getByRole("dialog").getByLabel("陈述", { exact: true }),
    ).toBeVisible()
  } finally {
    await adminContext.close()
  }
})
