import type { ChangeRequestsPublic } from "../src/client"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { createUser } from "./utils/privateApi"
import { addDraftingEvidence } from "./utils/sources"
import { logInUser, logOutUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

for (const width of [1440, 390]) {
  test(`bot previews, confirms and withdraws at ${width}px`, async ({
    page,
    request,
    modelUrl,
  }, testInfo) => {
    test.setTimeout(120000)
    await page.setViewportSize({ width: 1440, height: 900 })
    const password = "synthetic-password-2026"
    const email = `bot-${crypto.randomUUID()}@example.com`
    const token = await request.post("/api/v1/private/setup-tokens", {
      data: { purpose: "new_org" },
    })
    const initialized = await request.post("/api/v1/setup/initialize", {
      data: {
        token: (await token.json()).token,
        org_name: "机器人合成企业",
        admin_username: email,
        admin_password: password,
        admin_full_name: "管理员",
      },
    })
    expect(initialized.ok()).toBeTruthy()
    const { org_id: orgId, access_token: adminToken } = await initialized.json()
    const base = `/api/v1/orgs/${orgId}`
    const headers = { Authorization: `Bearer ${adminToken}` }
    const employeeEmail = `employee-${crypto.randomUUID()}@example.com`
    const uploaded = await request.post(`${base}/roster/imports`, {
      headers,
      multipart: {
        file: {
          name: "roster.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            `工号,姓名,部门,职位,上级工号,邮箱\nE01,合成员工,研发部,工程师,,${employeeEmail}\n`,
          ),
        },
      },
    })
    const roster = await uploaded.json()
    expect(
      (
        await request.post(`${base}/roster/imports/${roster.id}/apply`, {
          headers,
          data: { base_revision: roster.base_revision },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.post(`${base}/capability-schema`, {
          headers,
          data: { template_id: "tech-rnd" },
        })
      ).ok(),
    ).toBeTruthy()
    expect(
      (
        await request.post(`${base}/accounts/issue`, { headers, data: {} })
      ).ok(),
    ).toBeTruthy()
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
    expect(
      (
        await request.put(`${base}/drafting-settings`, {
          headers,
          data: { enabled: true },
        })
      ).ok(),
    ).toBeTruthy()
    await logInUser(page, email, password)
    await addDraftingEvidence(page)
    expect(
      (
        await request.post(`${base}/initial-profiles/draft`, {
          headers,
          data: { scope: "all" },
        })
      ).status(),
    ).toBe(202)
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`${base}/initial-profiles/progress`, {
                headers,
              })
            ).json()
          ).counts.generated,
        { timeout: 60000 },
      )
      .toBe(1)

    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
    await page.goto("/admin/bot")
    await page.getByLabel("提问指南").fill("请询问具体合作经历")
    await page.getByText("高级设置：聊天原文保留", { exact: true }).click()
    await page.getByLabel("聊天原文保留天数").fill("3")
    await page.getByRole("button", { name: "保存", exact: true }).click()
    await expect(page.getByRole("status")).toContainText("机器人设置已保存")
    await page.reload()
    await expect(page.getByLabel("提问指南")).toHaveValue("请询问具体合作经历")
    await expect(page.getByLabel("聊天原文保留天数")).toHaveValue("3")
    await expectChineseCopy(page, ["synthetic"])
    const accounts = await (
      await request.get(`${base}/accounts`, { headers })
    ).json()
    const account = accounts.data[0]
    const reset = await request.post(
      `${base}/accounts/${account.account.user_id}/reset-password`,
      { headers },
    )
    const oldPassword = (await reset.json()).initial_password
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: employeeEmail, password: oldPassword },
    })
    const changed = await request.patch("/api/v1/users/me/password", {
      headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
      data: { current_password: oldPassword, new_password: password },
    })
    expect(changed.ok()).toBeTruthy()
    const employeeHeaders = {
      Authorization: `Bearer ${(await changed.json()).access_token}`,
    }
    const readRequests = async (): Promise<ChangeRequestsPublic["data"]> =>
      (
        await (
          await request.get(`${base}/me/requests?status=all`, {
            headers: employeeHeaders,
          })
        ).json()
      ).data
    const initial = (await readRequests())[0]
    expect(initial.status).toBe("pending_owner")
    expect(initial.origin).toBe("initial_sync")
    expect(initial.items?.every((item) => item.after?.level != null)).toBe(true)
    await logOutUser(page)
    await logInUser(page, employeeEmail, password)
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole("button", { name: "打开画像机器人" }).click()
    let panel = page.getByRole("dialog", { name: "我的画像机器人" })
    await panel.getByLabel("发给机器人的消息").fill("请帮我过目初始画像")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      panel.getByRole("region", { name: "提交前预览" }),
    ).toBeVisible()
    // The fake Pi model claims submission; only the card action can actually submit.
    await expect(panel.getByText("已提交", { exact: true })).toBeVisible()
    expect((await readRequests())[0].status).toBe("pending_owner")
    await panel.getByRole("button", { name: "继续修改", exact: true }).click()
    await expect(panel.getByText("请告诉我需要修改的内容")).toBeVisible()
    await panel.getByLabel("发给机器人的消息").fill("补充一段合成项目经历")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    await expect(
      panel.getByText("本人完成了合成项目的验证", { exact: false }),
    ).toBeVisible()
    const current = (await readRequests())[0]
    expect(current.id).toBe(initial.id)
    expect(current.status).toBe("draft")
    // Another tab changes the request after preview, forcing a new preview.
    expect(
      (
        await request.put(`${base}/me/requests/${initial.id}/items`, {
          headers: employeeHeaders,
          data: {
            items: [
              {
                op: "add",
                capability_key: "automated_testing",
                statement: "另一标签页的经历",
                owner_statement: "最新依据",
              },
            ],
          },
        })
      ).ok(),
    ).toBeTruthy()
    await panel
      .getByRole("button", { name: "确认提交", exact: true })
      .last()
      .click()
    await expect(
      panel.getByText("内容已变化，请重新预览后再确认。"),
    ).toBeVisible()
    expect((await readRequests())[0].status).toBe("draft")
    await panel.getByRole("button", { name: "重新预览最新内容" }).click()
    await expect(
      panel.getByText("另一标签页的经历", { exact: false }),
    ).toBeVisible()
    await page.reload()
    await page.getByRole("button", { name: "打开画像机器人" }).click()
    panel = page.getByRole("dialog", { name: "我的画像机器人" })
    await expect(
      panel.getByText("另一标签页的经历", { exact: false }),
    ).toBeVisible()
    await expectChineseCopy(page)
    const bounds = await panel.boundingBox()
    expect(bounds!.width).toBeLessThanOrEqual(width)
    if (width === 390) expect(bounds!.width).toBe(width)
    await expect
      .poll(async () => Math.round((await panel.boundingBox())!.x))
      .toBe(width === 390 ? 0 : width - 460)
    await page.screenshot({
      path: testInfo.outputPath(`bot-${width}.png`),
      animations: "disabled",
    })
    await panel
      .getByRole("button", { name: "确认提交", exact: true })
      .last()
      .click()
    await expect(
      panel.getByText("已提交给经理审批，审批前可以撤回。"),
    ).toBeVisible()
    const sent = (await readRequests())[0]
    expect(sent.status).toBe("pending_review")
    expect(sent.submitted_via).toBe("bot")
    await panel.getByRole("button", { name: "撤回", exact: true }).click()
    await expect(panel.getByRole("heading", { name: "已撤回" })).toBeVisible()
    expect((await readRequests())[0].status).toBe("withdrawn")
    const storage = await page.evaluate(() => ({ ...sessionStorage }))
    expect(JSON.stringify(storage)).not.toContain("合成项目经历")
  })
}

test("disabled model keeps the entry and points to the form", async ({
  page,
}) => {
  const email = `empty-bot-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  await logInUser(page, email, password)
  await page.getByRole("button", { name: "打开画像机器人" }).click()
  const panel = page.getByRole("dialog")
  await expect(panel.getByRole("link", { name: "我的画像" })).toBeVisible()
  await panel.getByLabel("发给机器人的消息").fill("你好")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    panel.getByText("机器人需要先配置模型，你也可以在画像页直接修改"),
  ).toBeVisible()
  await expectChineseCopy(page)
  const question = {
    type: "question_card",
    id: crypto.randomUUID(),
    question: "请补充合作经历",
    reason: "需要本人确认",
    status: "open",
    actions: ["answer_question"],
  }
  await page.route("**/bot/sessions/*/turns?*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body:
        JSON.stringify({ type: "final", response: { messages: [question] } }) +
        "\n",
    }),
  )
  await panel.getByLabel("发给机器人的消息").fill("查看待确认问题")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await panel.getByLabel("确认后的回答").fill("本人完成了跨部门合成项目")
  await page.route("**/bot/sessions/*/actions?*", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      action: "answer_question",
      question_id: question.id,
      answer: "本人完成了跨部门合成项目",
    })
    await route.fulfill({
      json: { messages: [{ ...question, status: "answered", actions: [] }] },
    })
  })
  await panel.getByRole("button", { name: "确认回答" }).click()
  await expect(panel.getByText("问题已处理")).toBeVisible()
  await expectChineseCopy(page)
})

test("web bot directs reviews to the review page without decision buttons", async ({
  page,
}) => {
  const email = `review-bot-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  await createUser({ email, password, withEmptyProfile: true })
  await logInUser(page, email, password)
  await page.route("**/bot/sessions/*/turns?*", (route) =>
    route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({
        type: "final",
        response: {
          messages: [
            {
              type: "text",
              text: "请在网页审批页查看并决定",
              review_link: "/reviews",
            },
            {
              type: "review_card",
              id: crypto.randomUUID(),
              revision: 1,
              items: [],
              decisions: [],
              actions: [],
            },
          ],
        },
      })}\n`,
    }),
  )
  await page.getByRole("button", { name: "打开画像机器人" }).click()
  const panel = page.getByRole("dialog")
  await panel.getByLabel("发给机器人的消息").fill("查看待审批请求")
  await panel.getByRole("button", { name: "发送", exact: true }).click()
  await expect(panel.getByRole("link", { name: "去审批页" })).toHaveCount(2)
  await expect(
    panel.getByRole("link", { name: "去审批页" }).first(),
  ).toHaveAttribute("href", "/reviews")
  await expect(panel.getByRole("button", { name: "确认决定" })).toHaveCount(0)
  await expectChineseCopy(page)
})
