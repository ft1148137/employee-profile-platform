import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { EmployeeAccount } from "../src/client"

test.use({ storageState: { cookies: [], origins: [] } })

test("manager views and directly edits a team profile", async ({
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
      org_name: "合成团队企业",
      admin_username: `review-${crypto.randomUUID()}@example.com`,
      admin_password: password,
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const post = async (path: string, data: unknown) => {
    const response = await request.post(path, { headers, data })
    expect(response.ok()).toBeTruthy()
    return response
  }
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
  await post(`${base}/roster/imports/${preview.id}/apply`, {
    base_revision: preview.base_revision,
  })
  await post(`${base}/capabilities/versions`, {
    expected_version: 0,
    definitions: [
      {
        key: "skill_1",
        name: "合成能力1",
        category: "工程",
        description: "合成能力",
        level_rubric: {
          levels: [
            { key: "basic", label: "基础", description: "在指导下完成" },
            { key: "routine", label: "独立完成", description: "能独立完成" },
          ],
        },
      },
    ],
  })
  await post(`${base}/accounts/issue`, {})
  const accounts: EmployeeAccount[] = (
    await (await request.get(`${base}/accounts`, { headers })).json()
  ).data
  const subject = accounts.find((value) => value.employee_code === "E")!
  const manager = accounts.find((value) => value.employee_code === "M")!
  await post(`${base}/role-grants`, {
    user_id: manager.account!.user_id,
    role: "dept_manager",
    department_id: manager.department!.id,
  })
  const reset = await (
    await post(
      `${base}/accounts/${manager.account!.user_id}/reset-password`,
      {},
    )
  ).json()
  const login = await request.post("/api/v1/login/access-token", {
    form: { username: reset.username, password: reset.initial_password },
  })
  expect(login.ok()).toBeTruthy()
  const changed = await request.patch("/api/v1/users/me/password", {
    headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
    data: { current_password: reset.initial_password, new_password: password },
  })
  expect(changed.ok()).toBeTruthy()
  const managerToken = (await changed.json()).access_token
  await post(`${base}/employees/${subject.id}/profile/direct-edits`, {
    base_revision: null,
    items: [
      {
        op: "add",
        capability_key: "skill_1",
        statement: "在指导下完成联调",
        level: "basic",
      },
    ],
  })
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const last = body.messages.at(-1)
    const previous = last.role === "tool" ? last.tool_call_id : null
    const result = previous ? JSON.parse(last.content) : null
    const name = !previous
      ? "list_team_members"
      : previous === "list_team_members"
        ? "get_member_profile"
        : "prepare_profile_edit"
    const args =
      name === "list_team_members"
        ? { q: "合成员工" }
        : name === "get_member_profile"
          ? { employee_id: result.data[0].employee_id }
          : {
              employee_id: result.employee_id,
              base_revision: result.revision,
              items: [
                {
                  op: "modify",
                  capability_key: "skill_1",
                  statement: "独立完成接口联调",
                  level: "routine",
                },
              ],
            }
    const done = previous === "prepare_profile_edit"
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({ id: "team", object: "chat.completion.chunk", choices: [{ index: 0, delta: done ? { role: "assistant", content: "请核对改动" } : { role: "assistant", tool_calls: [{ index: 0, id: name, type: "function", function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: done ? "stop" : "tool_calls" }] })}\n\ndata: [DONE]\n\n`,
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
    await page.goto("/login")
    await page.evaluate(
      ({ accessToken, orgId }) => {
        localStorage.setItem("access_token", accessToken)
        localStorage.setItem("current_org_id", orgId)
      },
      { accessToken: managerToken, orgId: admin.org_id },
    )
    await page.goto("/assistant")
    const panel = page.getByRole("region", { name: "助手", exact: true })
    await expect(panel).toContainText("可以操作你本人和你管辖部门员工的画像")
    await expect(panel).not.toContainText("只操作你本人的画像")
    await panel
      .getByLabel("发给机器人的消息")
      .fill("查看合成员工的画像，改为独立完成")
    await panel.getByRole("button", { name: "发送", exact: true }).click()
    const card = panel.getByRole("region", { name: "画像修改确认" })
    await expect(card).toContainText("合成员工")
    await expect(card).toContainText("在指导下完成联调")
    await expect(card).toContainText("独立完成接口联调")
    await expect(card).toContainText("等级：基础")
    await expect(card).toContainText("等级：独立完成")
    await expect(card).not.toContainText("routine")
    await card.getByRole("button", { name: "确认修改", exact: true }).click()
    await expect(
      panel.getByText("画像已发布，已通知员工", { exact: true }),
    ).toBeVisible()
    await page.goto(`/employees/${subject.id}`)
    await expect(
      page
        .getByRole("article", { name: "合成能力1" })
        .getByText("等级：独立完成", { exact: true }),
    ).toBeVisible()
    const profile = await (
      await request.get(`${base}/employees/${subject.id}/profile`, {
        headers,
      })
    ).json()
    expect(profile.latest_published.revision).toBe(2)
    expect(profile.items[0].claim.assessment_status).toBe("confirmed")
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
