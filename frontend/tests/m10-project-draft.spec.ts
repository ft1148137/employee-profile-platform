import { createServer } from "node:http"
import { expect, test } from "@playwright/test"
import { setupProjectManagers } from "./utils/projects"

test.use({ storageState: { cookies: [], origins: [] } })

test("从网页助手核对项目草稿并创建项目需求", async ({ page, request }) => {
  test.setTimeout(90_000)
  const { base, open, adminToken, managerToken, departments } =
    await setupProjectManagers(request)
  const title = "合成客户报表项目"
  const project = {
    title,
    requesting_department_id: departments.A,
    roles: [
      {
        role_name: "接口开发",
        target_department_id: departments.B,
        capability_key: "delivery",
        minimum_level: "routine",
        headcount: 1,
        allocation_percent: 50,
        window_start: "2026-12-01",
        window_end: "2026-12-02",
        description: "实现报表接口",
      },
      {
        role_name: "报表验证",
        target_department_id: departments.C,
        capability_key: "validation",
        headcount: 1,
        allocation_percent: 30,
        window_start: "2026-12-03",
        window_end: "2026-12-04",
        depends_on: [0],
        description: "核对报表结果",
      },
    ],
  }
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const last = body.messages.at(-1)
    const previous = last?.tool_call_id
    const progress = JSON.stringify(
      body.messages.findLast(
        (message: { role: string }) => message.role === "user",
      ),
    ).includes("多角色项目进展")
    const admin = body.tools?.some(
      (tool: { function: { name: string } }) =>
        tool.function.name === "admin_read",
    )
    const scope = admin ? "admin_read" : "coordination"
    const projects =
      previous === "list_projects" ? JSON.parse(last.content).data : undefined
    const projectRow = (admin ? projects?.data : projects)?.[0]
    const report =
      previous === "get_project" ? JSON.parse(last.content) : undefined
    const detail = report?.data ?? report
    const name = !previous
      ? "get_capability_dictionary"
      : previous === "get_capability_dictionary"
        ? "list_capacity_departments"
        : "coordination"
    const done = progress
      ? previous === "get_project"
      : previous === "coordination"
    const functionName = projectRow ? "get_project" : "list_projects"
    const input = projectRow ? { project_id: projectRow.id } : {}
    const query = admin
      ? { function: functionName, args: input }
      : { function: functionName, arguments: input }
    const args =
      name === "coordination"
        ? { function: "prepare_project_draft", arguments: project }
        : {}
    const delta = done
      ? {
          role: "assistant",
          content: progress
            ? `[${detail.title}](/capacity?tab=projects&project=${detail.id})：${detail.roles.length} 个角色（${detail.roles.map((role: { role_name: string }) => role.role_name).join("、")}）`
            : "请核对项目草稿",
        }
      : {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: progress ? functionName : name,
              type: "function",
              function: {
                name: progress ? scope : name,
                arguments: JSON.stringify(progress ? query : args),
              },
            },
          ],
        }
    const chunk = {
      id: "project-draft",
      object: "chat.completion.chunk",
      choices: [
        { index: 0, delta, finish_reason: done ? "stop" : "tool_calls" },
      ],
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`)
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  try {
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing synthetic model address")
    const host = process.env.MODEL_TEST_HOST ?? "127.0.0.1"
    const setting = await request.put(`${base}/model-setting`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        enabled: true,
        kind: "openai_compatible",
        provider: "openai-compatible",
        model: "synthetic",
        base_url: `http://${host}:${address.port}/v1`,
        api_key: "synthetic-key",
      },
    })
    expect(setting.ok(), await setting.text()).toBeTruthy()

    await open(page, "A", "/assistant")
    const assistant = page.getByRole("region", { name: "助手", exact: true })
    await assistant
      .getByLabel("发给助手的消息")
      .fill(
        "起草一个客户报表项目。研发先做接口，12 月 1–2 日一人投入 50%，测试接着验证，12 月 3–4 日一人投入 30%。",
      )
    await assistant.getByRole("button", { name: "发送", exact: true }).click()
    const draft = assistant.getByRole("region", { name: "项目草稿" })
    await expect(draft).toContainText(title)
    await expect(draft).toContainText("前置角色：1. 接口开发")
    await expect(draft).not.toContainText("合成研发经理")
    const before = await request.get(`${base}/capacity/projects`, {
      headers: { Authorization: `Bearer ${managerToken}` },
    })
    expect(before.ok()).toBeTruthy()
    expect(await before.json()).toEqual([])
    await draft.getByRole("button", { name: "确认创建项目" }).click()
    await expect(assistant.getByText("项目已创建。")).toBeVisible()

    const askProgress = async (admin: boolean) => {
      const input = page.getByLabel(
        admin ? "发给管理助手的消息" : "发给助手的消息",
      )
      await input.fill("查询多角色项目进展")
      await page.getByRole("button", { name: "发送", exact: true }).click()
      const log = page.getByRole("log")
      await expect(log).toContainText("2 个角色（接口开发、报表验证）")
      return log.getByRole("link", { name: title, exact: true })
    }

    await open(page, "admin", "/assistant")
    await expect(
      page.getByRole("navigation", { name: "助手模式" }),
    ).toHaveCount(0)
    const adminLink = await askProgress(true)
    const table = page
      .getByRole("table")
      .filter({ has: page.getByRole("columnheader", { name: "项目名称" }) })
    await expect(table.getByRole("columnheader")).toHaveText([
      "项目名称",
      "发起部门",
      "创建时间",
    ])
    const expectedLink = await adminLink
      .and(table.getByRole("link"))
      .getAttribute("href")
    expect(expectedLink).toMatch(/^\/capacity\?tab=projects&project=/)
    await table.getByRole("link", { name: title, exact: true }).click()
    await expect(page).toHaveURL(expectedLink!)
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible()

    await open(page, "A", "/assistant")
    await expect(
      page.getByRole("navigation", { name: "助手模式" }),
    ).toHaveCount(0)
    await expect(await askProgress(false)).toHaveAttribute(
      "href",
      expectedLink!,
    )

    const manager = await request.get("/api/v1/users/me", {
      headers: { Authorization: `Bearer ${managerToken}` },
    })
    expect(manager.ok()).toBeTruthy()
    const grant = await request.post(`${base}/role-grants`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { user_id: (await manager.json()).id, role: "org_admin" },
    })
    expect(grant.ok(), await grant.text()).toBeTruthy()
    await open(page, "A", "/assistant")
    const modes = page.getByRole("navigation", { name: "助手模式" })
    await expect(
      modes.getByRole("link", { name: "个人与团队" }),
    ).toHaveAttribute("aria-current", "page")
    await modes.getByRole("link", { name: "企业管理" }).click()
    await expect(page).toHaveURL("/assistant?mode=admin")
    await expect(page.getByLabel("发给管理助手的消息")).toBeEnabled()
    await modes.getByRole("link", { name: "个人与团队" }).click()
    await expect(page).toHaveURL("/assistant?mode=personal")
    await expect(await askProgress(false)).toHaveAttribute(
      "href",
      expectedLink!,
    )
    const org = base.split("/").pop()!
    const draftToken = "synthetic-feishu-draft"
    await page.route("**/bot/sessions/project-drafts/feishu?*", (route) => {
      const query = new URL(route.request().url()).searchParams
      expect(query.get("draft")).toBe(draftToken)
      expect(query.get("org_id")).toBe(org)
      return route.fulfill({
        json: { type: "project_draft_card", project, fields: [] },
      })
    })
    await page.goto(`/assistant?draft=${draftToken}&org=${org}`)
    await expect(page.getByRole("region", { name: "项目草稿" })).toContainText(
      title,
    )
    await page.getByRole("link", { name: "组织", exact: true }).click()
    await expect(
      page.getByRole("button", { name: "打开画像机器人" }),
    ).toHaveCount(0)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
