import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"
import { expectChineseCopy } from "./utils/copy"

test.use({ storageState: { cookies: [], origins: [] } })

for (const reviewer of ["manager", "admin", "delegate"]) {
  test(`${reviewer}: mixed decisions, returned draft and timelines on mobile`, async ({
    page,
    request,
  }, info) => {
    test.setTimeout(120000)
    const password = "synthetic-review-password-2026"
    const token = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: (await token.json()).token,
        org_name: "合成审批企业",
        admin_username: `review-${crypto.randomUUID()}@example.com`,
        admin_password: password,
        admin_full_name: "合成管理员",
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const admin = await initialized.json()
    const base = `/api/v1/orgs/${admin.org_id}`
    const headers = { Authorization: `Bearer ${admin.access_token}` }
    const roster = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,职位,上级工号,邮箱\nM,合成经理,研发部,经理,,manager-${crypto.randomUUID()}@example.com\nE,合成员工,研发部,工程师,M,employee-${crypto.randomUUID()}@example.com\n`,
          ),
        },
      },
    })
    const preview = await roster.json()
    expect(
      (
        await request.post(`${base}/roster/imports/${preview.id}/apply`, {
          headers,
          data: { base_revision: preview.base_revision },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.post(`${base}/capabilities/versions`, {
          headers,
          data: {
            expected_version: 0,
            definitions: [1, 2, 3].map((number) => ({
              key: `skill_${number}`,
              name: `合成能力${number}`,
              category: "合成能力",
              description: "审批测试",
              level_rubric: {
                levels: [
                  {
                    key: "routine",
                    label: "独立完成",
                    description: "能独立完成",
                  },
                ],
              },
            })),
          },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.post(`${base}/accounts/issue`, { headers, data: {} })
      ).ok(),
    ).toBeTruthy()
    const accounts: EmployeeAccount[] = (
      await (await request.get(`${base}/accounts`, { headers })).json()
    ).data
    const subject = accounts.find((value) => value.employee_code === "E")!
    const manager = accounts.find((value) => value.employee_code === "M")!
    expect(
      (
        await request.post(`${base}/role-grants`, {
          headers,
          data: {
            user_id: manager.account!.user_id,
            role: "dept_manager",
            department_id: manager.department!.id,
          },
        })
      ).ok(),
    ).toBeTruthy()
    const login = async (account: EmployeeAccount) => {
      const reset = await (
        await request.post(
          `${base}/accounts/${account.account!.user_id}/reset-password`,
          { headers },
        )
      ).json()
      const result = await (
        await request.post("/api/v1/login/access-token", {
          form: { username: reset.username, password: reset.initial_password },
        })
      ).json()
      return (
        await (
          await request.patch("/api/v1/users/me/password", {
            headers: { Authorization: `Bearer ${result.access_token}` },
            data: {
              current_password: reset.initial_password,
              new_password: password,
            },
          })
        ).json()
      ).access_token
    }
    const ownerToken = await login(subject)
    const reviewerToken =
      reviewer === "manager" ? await login(manager) : admin.access_token
    const enter = async (accessToken: string, path: string) => {
      await page.goto("/login")
      await page.evaluate(
        ({ accessToken, orgId }) => {
          localStorage.setItem("access_token", accessToken)
          localStorage.setItem("current_org_id", orgId)
        },
        { accessToken, orgId: admin.org_id },
      )
      await page.goto(path)
    }
    if (reviewer === "delegate") {
      await enter(await login(manager), "/settings")
      await page.getByRole("tab", { name: "代理审批", exact: true }).click()
      await page
        .getByLabel("代理人", { exact: true })
        .selectOption({ label: "合成管理员" })
      const localDate = (offset: number) =>
        new Date(Date.now() + offset - new Date().getTimezoneOffset() * 60000)
          .toISOString()
          .slice(0, 16)
      await page.getByLabel("开始时间", { exact: true }).fill(localDate(-60000))
      await page
        .getByLabel("结束时间", { exact: true })
        .fill(localDate(86400000))
      await page.getByRole("button", { name: "保存代理", exact: true }).click()
      await expect(page.getByLabel("代理记录")).toContainText("代理中")
      await page.reload()
      await page.getByRole("tab", { name: "代理审批", exact: true }).click()
      await expect(page.getByLabel("代理记录")).toContainText("合成管理员")
      await expectChineseCopy(page)
    }
    const ownerHeaders = { Authorization: `Bearer ${ownerToken}` }
    if (reviewer === "admin") {
      const grants = await (
        await request.get(`${base}/role-grants`, { headers })
      ).json()
      for (const grant of grants.filter(
        (value: { role: string }) => value.role === "dept_manager",
      ))
        expect(
          (
            await request.delete(`${base}/role-grants/${grant.id}`, { headers })
          ).ok(),
        ).toBeTruthy()
    }
    const draft = await (
      await request.post(`${base}/me/requests`, {
        headers: ownerHeaders,
        data: {
          items: [1, 2, 3].map((number) => ({
            op: "add",
            capability_key: `skill_${number}`,
            statement: `合成陈述${number}`,
            level: "routine",
            owner_statement: "本人确认的合成依据",
          })),
        },
      })
    ).json()
    const submitted = await request.post(
      `${base}/me/requests/${draft.id}/submit`,
      { headers: ownerHeaders, data: { revision: draft.revision } },
    )
    expect(submitted.ok()).toBeTruthy()
    await enter(ownerToken, `/reviews/${draft.id}`)
    await expect(page.getByRole("alert")).toContainText("404")
    await expect(page.getByRole("link", { name: /^待审批/ })).toHaveCount(0)
    await enter(reviewerToken, "/inbox")
    await page.getByRole("link", { name: "查看并审批", exact: true }).click()
    await expect(page.getByRole("heading", { name: "逐项审批" })).toBeVisible()
    if (reviewer === "delegate")
      await expect(
        page.getByText("代合成经理审批", { exact: true }),
      ).toBeVisible()
    if (reviewer === "manager") {
      await page.route(`**/orgs/${admin.org_id}/reviews`, async (route) => {
        const data = await (await route.fetch()).json()
        data.data[0].can_decide = false
        data.data[0].unavailable_reason = "没有可用的审批人"
        await route.fulfill({ json: data })
      })
      await page.goto("/reviews")
      await expect(
        page.getByText("没有可用的审批人", { exact: true }),
      ).toBeVisible()
      await expect(
        page.getByRole("link", { name: "查看并审批", exact: true }),
      ).toHaveCount(0)
      await page.unroute(`**/orgs/${admin.org_id}/reviews`)
    }
    await page.goto("/reviews")
    await expect(
      page.getByRole("listitem").filter({ hasText: "合成员工" }),
    ).toContainText("3 项改动")
    await page.getByRole("link", { name: "查看并审批", exact: true }).click()
    await page.setViewportSize({ width: 390, height: 844 })
    const choose = async () => {
      await page.getByLabel("合成能力1的决定").selectOption("approved")
      await page.getByLabel("合成能力2的决定").selectOption("changes_requested")
      await page.getByLabel("合成能力3的决定").selectOption("rejected")
      await expect(
        page.getByRole("button", { name: "核对并提交" }),
      ).toBeDisabled()
      await page.getByLabel("合成能力2的原因").fill("请补充独立完成的依据")
      await page.getByLabel("合成能力3的原因").fill("依据与陈述不符")
    }
    if (reviewer === "manager") {
      const pattern = `**/api/v1/orgs/${admin.org_id}/reviews/${draft.id}`
      await page.route(pattern, async (route) => {
        const response = await route.fetch()
        const data = await response.json()
        data.owner_reviewed = false
        data.items[0].jira_only = true
        data.items[1].approvable = false
        data.items[1].approval_blocked_reason = "目标判断已被其他请求取代"
        await route.fulfill({ json: data })
      })
      await page.reload()
      await expect(
        page.getByText("只有 Jira 来源，批准后仍为暂定"),
      ).toBeVisible()
      await expect(page.getByText("员工未过目", { exact: true })).toBeVisible()
      await expect(
        page.getByLabel("合成能力2的决定").locator('option[value="approved"]'),
      ).toHaveAttribute("disabled", "")
      await expectChineseCopy(page)
      await page.unroute(pattern)
      await page.reload()
    }
    await choose()
    for (const button of await page
      .getByRole("button", { name: "员工依据说明", exact: true })
      .all()) {
      await button.click()
      await expect(page.getByRole("dialog")).toContainText("本人确认的合成依据")
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "关闭", exact: true })
        .click()
    }
    if (reviewer === "manager") {
      await page.route(
        `**/reviews/${draft.id}/decisions`,
        (route) =>
          route.fulfill({
            status: 409,
            json: { detail: "内容已变化，请重新查看" },
          }),
        { times: 1 },
      )
      await page.route(
        `**/api/v1/orgs/${admin.org_id}/reviews/${draft.id}`,
        (route) =>
          route.fulfill({ status: 403, json: { detail: "你不是当前审批人" } }),
        { times: 1 },
      )
      await page.getByRole("button", { name: "核对并提交" }).click()
      await page.getByRole("button", { name: "确认提交决定" }).click()
      await expect(page.getByRole("alert")).toContainText(
        "内容已变化，请重新查看",
      )
      await expect(page.getByLabel("合成能力1的决定")).toHaveCount(0)
      await page.reload()
      await expect(page.getByLabel("合成能力1的决定")).toHaveValue("")
      await choose()
    }
    await page.getByRole("button", { name: "核对并提交" }).click()
    await expect(page.getByRole("dialog")).toContainText("合成能力2：退回修改")
    await expect(page.getByRole("dialog")).toContainText("请补充独立完成的依据")
    await page.getByRole("button", { name: "确认提交决定" }).click()
    await expect(page.getByRole("status")).toContainText("审批已提交")
    if (reviewer === "admin") {
      await page.setViewportSize({ width: 1280, height: 900 })
      await page.getByRole("link", { name: "员工", exact: true }).click()
      await page.getByRole("row").filter({ hasText: "合成员工" }).click()
      await page.getByRole("tab", { name: "证据", exact: true }).click()
      await page.getByLabel("证据类型").selectOption("self_report")
      let release = () => {}
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      const pattern = `**/api/v1/orgs/${admin.org_id}/evidence/*`
      await page.route(pattern, async (route) => {
        await gate
        await route.continue()
      })
      const fetched = page.waitForRequest((url) =>
        url.url().includes(`${base}/evidence/`),
      )
      await page
        .getByRole("tabpanel", { name: "证据", exact: true })
        .getByRole("button")
        .first()
        .click()
      await fetched
      try {
        expect(
          await page
            .getByRole("dialog")
            .getByText("本人确认的合成依据", { exact: true })
            .count(),
        ).toBe(0)
      } finally {
        release()
      }
      await expect(page.getByRole("dialog")).toContainText(
        "受限内容：仅本人和管辖该部门的经理可见",
      )
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "关闭", exact: true })
        .click()
      await page.unroute(pattern)
      await page.setViewportSize({ width: 390, height: 844 })
    }
    await enter(ownerToken, `/employees/${subject.id}`)
    const card = page.locator(`[id="request-${draft.id}"]`)
    await expect(card).toContainText("合成能力1：批准")
    await expect(card).toContainText("合成能力2：退回修改")
    await expect(card).toContainText("原因：依据与陈述不符")
    await card.getByText("请求时间线", { exact: true }).click()
    await expect(card.getByRole("list", { name: "请求时间线" })).toContainText(
      "发布画像 · r1",
    )
    await expect(card).toContainText(
      `审批决定 · ${reviewer === "manager" ? "合成经理" : "合成管理员"}`,
    )
    await card.getByRole("link", { name: "查看退回草稿" }).click()
    await expectChineseCopy(page)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy()
    await page.screenshot({
      path: info.outputPath("review-results-mobile.png"),
      fullPage: true,
    })
    await page.getByRole("tab", { name: "版本历史" }).click()
    await expect(
      page.getByRole("tabpanel", { name: "版本历史", exact: true }),
    ).toContainText("r1")
    await page.getByRole("button", { name: "来源请求" }).click()
    await expect(card).toBeVisible()
    await page.getByRole("button", { name: "继续修改" }).click()
    const editor = page.getByRole("dialog")
    await editor
      .getByRole("textbox", { name: "依据说明", exact: true })
      .fill("本人补充了独立完成的合成依据")
    await editor
      .getByRole("button", { name: "预览并提交", exact: true })
      .click()
    await editor.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(editor).toBeHidden()
    expect(
      (
        await (
          await request.get(`${base}/me/requests`, { headers: ownerHeaders })
        ).json()
      ).data.some(
        (value: { parent_request_id: string; status: string }) =>
          value.parent_request_id === draft.id &&
          value.status === "pending_review",
      ),
    ).toBeTruthy()
    if (reviewer === "delegate") {
      await enter(await login(manager), "/settings")
      await page.getByRole("tab", { name: "代理审批", exact: true }).click()
      await page.getByRole("button", { name: "撤销代理", exact: true }).click()
      await expect(page.getByLabel("代理记录")).toContainText("已撤销")
    }
  })
}
