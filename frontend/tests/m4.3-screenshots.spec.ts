import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import path from "node:path"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({
  storageState: { cookies: [], origins: [] },
  viewport: { width: 1440, height: 900 },
})

test("M4.3 screenshots: file preview, web approval, profiles, settings and direct editing", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(120000)
  const password = "synthetic-review-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "画像协作演示企业",
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
          definitions: [1].map((number) => ({
            key: `skill_${number}`,
            name: "接口设计与联调",
            category: "合成能力",
            description: "设计接口并完成联调",
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
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
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
  const managerToken = await login(manager)

  // Only the model transport is synthetic; uploads, tools and confirmation use real services.
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const last = body.messages.at(-1)
    const previous = last.role === "tool" ? last.tool_call_id : null
    const result = previous ? JSON.parse(last.content) : null
    const reviewing = body.messages.some(
      (message: { role: string; content: unknown }) =>
        message.role === "user" &&
        JSON.stringify(message.content).includes("批准这条请求"),
    )
    if (!reviewing && !previous)
      expect(raw).toContain("我独立完成订单接口设计与联调")
    const name = reviewing
      ? !previous
        ? "list_pending_reviews"
        : previous === "list_pending_reviews"
          ? "get_review"
          : "prepare_review_decision"
      : "draft_update_request"
    const args = !reviewing
      ? {
          items: [
            {
              op: "add",
              capability_key: "skill_1",
              statement: "独立完成订单接口设计与联调",
              level: "routine",
              owner_statement: "本人确认工作总结中的项目经历",
            },
          ],
        }
      : name === "list_pending_reviews"
        ? {}
        : name === "get_review"
          ? { request_id: result.data[0].id }
          : {
              request_id: result.id,
              revision: result.revision,
              decisions: result.items.map(
                (item: { after: { id: string } }) => ({
                  item_id: item.after.id,
                  decision: "approved",
                }),
              ),
            }
    const done =
      previous ===
      (reviewing ? "prepare_review_decision" : "draft_update_request")
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({ id: "screenshots", object: "chat.completion.chunk", choices: [{ index: 0, delta: done ? { role: "assistant", content: reviewing ? "请核对审批决定，再点击确认。" : "已根据工作总结整理画像更新，请核对后提交。" } : { role: "assistant", tool_calls: [{ index: 0, id: name, type: "function", function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: done ? "stop" : "tool_calls" }] })}\n\ndata: [DONE]\n\n`,
    )
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  const address = server.address()
  if (!address || typeof address === "string")
    throw new Error("Missing model address")
  const host =
    process.env.MODEL_TEST_HOST ??
    (baseURL?.includes("backend:")
      ? Object.values(networkInterfaces())
          .flat()
          .find((network) => network?.family === "IPv4" && !network.internal)
          ?.address
      : "localhost")
  const enter = async (token: string, destination: string) => {
    await page.goto("/login")
    await page.evaluate(
      ({ token, org }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", org)
      },
      { token, org: admin.org_id },
    )
    await page.goto(destination)
  }
  const screenshot = async (name: string) => {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({
      path: path.resolve(
        import.meta.dirname,
        `../../docs/screenshots/m4.3/${name}.png`,
      ),
      animations: "disabled",
    })
  }
  try {
    expect(
      (
        await request.put(`${base}/model-setting`, {
          headers,
          data: {
            enabled: true,
            kind: "openai_compatible",
            provider: "openai-compatible",
            model: "synthetic",
            base_url: `http://${host}:${address.port}/v1`,
            api_key: "synthetic-key",
          },
        })
      ).ok(),
    ).toBeTruthy()
    await enter(ownerToken, "/assistant")
    await expect(page.getByRole("region", { name: "历史对话" })).toBeVisible()
    await expect(
      page
        .getByTestId("app-rail")
        .getByRole("link", { name: "我的团队", exact: true }),
    ).toHaveCount(0)
    const upload = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/attachments"),
    )
    await page.getByLabel("选择对话附件").setInputFiles({
      name: "工作总结.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# 工作总结\n我独立完成订单接口设计与联调。"),
    })
    expect((await upload).ok()).toBeTruthy()
    await expect(page.getByRole("list", { name: "待发送附件" })).toContainText(
      "已上传",
    )
    await page
      .getByLabel("发给机器人的消息")
      .fill("请根据工作总结整理我的画像更新")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const preview = page.getByRole("region", { name: "提交前预览" })
    await expect(preview).toContainText("独立完成订单接口设计与联调")
    await expect(
      preview.getByRole("button", { name: "确认提交", exact: true }),
    ).toBeEnabled()
    await expect(
      page.getByRole("log").getByLabel("附件：工作总结.md"),
    ).toBeVisible()
    await screenshot("employee-assistant")
    await preview.getByRole("button", { name: "确认提交", exact: true }).click()
    await expect(
      page.getByText("已提交给经理审批，审批前可以撤回。", { exact: true }),
    ).toBeVisible()
    const unpublished = await (
      await request.get(`${base}/employees/${subject.id}/profile`, { headers })
    ).json()
    expect(unpublished.latest_published).toBeNull()

    await enter(managerToken, "/assistant")
    await expect(page.getByRole("region", { name: "历史对话" })).toBeVisible()
    await expect(
      page
        .getByTestId("app-rail")
        .getByRole("link", { name: "我的团队", exact: true }),
    ).toBeVisible()
    await page.getByLabel("发给机器人的消息").fill("列出待审批，批准这条请求")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const review = page.getByRole("region", { name: "审批决定复述" })
    await expect(review).toContainText("决定：已批准")
    await expect(review).toContainText("等级：独立完成")
    await expect(
      review.getByRole("button", { name: "确认决定", exact: true }),
    ).toBeEnabled()
    await screenshot("manager-assistant")
    await review.getByRole("button", { name: "确认决定", exact: true }).click()
    await expect(
      page.getByText("审批决定已保存", { exact: false }),
    ).toBeVisible()
    const published = await (
      await request.get(`${base}/employees/${subject.id}/profile`, { headers })
    ).json()
    expect(published.latest_published.revision).toBe(1)

    await page.goto(`/employees/${subject.id}`)
    const capability = page.getByRole("article", {
      name: "接口设计与联调",
      exact: true,
    })
    await expect(capability).toContainText("等级：独立完成")
    await expect(
      capability.getByRole("button", { name: "直接修改", exact: true }),
    ).toBeVisible()
    await expect(capability).not.toContainText("独立完成订单接口设计与联调")
    await expect(capability).not.toContainText("依据")
    await expect(
      page.getByRole("tab", { name: "档案", exact: true }),
    ).toHaveCount(0)
    await screenshot("manager-team-profile")
    await page.goto("/settings")
    await page.getByRole("tab", { name: "档案", exact: true }).click()
    await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
      "研发部",
    )
    await expect(
      page.getByRole("tab", { name: "Jira 账号", exact: true }),
    ).toBeVisible()
    await screenshot("manager-settings")

    await enter(ownerToken, `/employees/${subject.id}`)
    await expect(capability).toContainText("等级：独立完成")
    await expect(capability).not.toContainText("本人确认工作总结")
    await expect(
      page.getByRole("button", { name: "直接修改", exact: true }),
    ).toHaveCount(0)
    await screenshot("employee-profile")
    await page.goto("/settings")
    await page.getByRole("tab", { name: "档案", exact: true }).click()
    await expect(page.getByRole("region", { name: "员工信息" })).toContainText(
      "研发部",
    )
    await expect(
      page.getByRole("tab", { name: "自动起草", exact: true }),
    ).toBeVisible()
    await screenshot("employee-settings")

    await enter(admin.access_token, `/employees/${subject.id}`)
    await capability
      .getByRole("button", { name: "直接修改", exact: true })
      .click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByRole("textbox", { name: "陈述", exact: true })
      .fill("独立完成订单及支付接口设计与联调")
    await dialog.getByRole("button", { name: "预览修改", exact: true }).click()
    await expect(dialog).toContainText("直接发布，不需要本人同意，会通知本人")
    await expect(
      dialog.getByRole("button", { name: "确认直接发布", exact: true }),
    ).toBeEnabled()
    await screenshot("admin-direct-edit")
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
