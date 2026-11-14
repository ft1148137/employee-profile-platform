import { createServer } from "node:http"
import { networkInterfaces } from "node:os"
import { expect, test } from "@playwright/test"
import type { ChangeSpec, EmployeeAccount } from "../src/client"
import { dictionaryDefinitions } from "./utils/model"

test.use({ storageState: { cookies: [], origins: [] } })

test("administrator grant applies after two confirmations in the assistant", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(120000)
  const setupToken = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await setupToken.json()).token,
      org_name: "确认卡合成企业",
      admin_username: `cards-${crypto.randomUUID()}@example.com`,
      admin_password: "synthetic-password-2026",
      admin_full_name: "合成管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const admin = await initialized.json()
  const base = `/api/v1/orgs/${admin.org_id}`
  const headers = { Authorization: `Bearer ${admin.access_token}` }
  const employeeEmail = `member-${crypto.randomUUID()}@example.com`
  const roster = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,邮箱\n001,合成员工,人事部,${employeeEmail}\n002,另一员工,运营部,other-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(roster.ok()).toBeTruthy()
  const preview = await roster.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  const issued = await request.post(`${base}/accounts/issue`, {
    headers,
    data: {},
  })
  expect(issued.ok()).toBeTruthy()
  const { data: people }: { data: EmployeeAccount[] } = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  const employee = people.find((person) => person.work_email === employeeEmail)!
  const target = employee.account!
  let proposedItems: ChangeSpec[] = [
    {
      function: "grant_role",
      args: { user_id: target.user_id, role: "org_admin" },
    },
  ]
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    const done = body.messages.at(-1).role === "tool" || !body.tools?.length
    res.writeHead(200, { "Content-Type": "text/event-stream" })
    res.end(
      `data: ${JSON.stringify({
        id: "synthetic-admin-card",
        object: "chat.completion.chunk",
        choices: [
          {
            index: 0,
            delta: done
              ? { role: "assistant", content: "请核对授权确认卡" }
              : {
                  role: "assistant",
                  tool_calls: [
                    {
                      index: 0,
                      id: "grant",
                      type: "function",
                      function: {
                        name: "propose_changes",
                        arguments: JSON.stringify({
                          items: proposedItems,
                        }),
                      },
                    },
                  ],
                },
            finish_reason: done ? "stop" : "tool_calls",
          },
        ],
      })}\n\ndata: [DONE]\n\n`,
    )
  })
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve))
  try {
    const address = server.address()
    if (!address || typeof address === "string")
      throw new Error("Missing address")
    const host =
      process.env.MODEL_TEST_HOST ??
      (baseURL?.includes("backend:")
        ? Object.values(networkInterfaces())
            .flat()
            .find((network) => network?.family === "IPv4" && !network.internal)
            ?.address
        : "localhost")
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
      ({ token, orgId }) => {
        localStorage.setItem("access_token", token)
        localStorage.setItem("current_org_id", orgId)
      },
      { token: admin.access_token, orgId: admin.org_id },
    )
    await page.goto("/admin/assistant")
    await page.getByLabel("发给管理助手的消息").fill("授予合成员工企业管理员")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const card = page.getByRole("region", { name: "设置改动确认卡" })
    await expect(card).toContainText("授予企业管理员", { timeout: 30000 })
    const roles = async () =>
      (await (
        await request.get(`${base}/role-grants`, { headers })
      ).json()) as {
        user_id: string
        role: string
      }[]
    const granted = async () =>
      (await roles()).some(
        (role) => role.user_id === target.user_id && role.role === "org_admin",
      )
    await card.getByRole("button", { name: "确认应用", exact: true }).click()
    await expect(card.getByRole("status")).toHaveText("请再次确认")
    expect(await granted()).toBe(false)
    await card.getByRole("button", { name: "再次确认", exact: true }).click()
    await expect(card.getByRole("status")).toHaveText("已应用")
    expect(await granted()).toBe(true)
    await page.reload()
    await expect(card.getByRole("status")).toHaveText("已应用")

    const definitions = [
      { ...dictionaryDefinitions[0], key: "new_skill", name: "新人带教" },
      { ...dictionaryDefinitions[0], key: "old_skill", name: "盘点作业" },
    ]
    const editor = await (
      await request.get(`${base}/capabilities/editor`, { headers })
    ).json()
    expect(
      (
        await request.put(`${base}/capabilities`, {
          headers,
          data: {
            definitions,
            expected_version: editor.expected_version,
            redraft: false,
          },
        })
      ).ok(),
    ).toBeTruthy()
    const departmentPath = `${base}/departments/${employee.department!.id}/capabilities`
    expect(
      (
        await request.put(departmentPath, {
          headers,
          data: { capability_keys: ["old_skill"] },
        })
      ).ok(),
    ).toBeTruthy()
    proposedItems = [
      {
        function: "set_department_capabilities",
        args: {
          department_id: employee.department!.id,
          capability_keys: ["new_skill"],
        },
      },
      {
        function: "set_department_capabilities",
        args: {
          department_id: people.find((person) => person.id !== employee.id)!
            .department!.id,
          capability_keys: [],
        },
      },
    ]
    await page.getByLabel("发给管理助手的消息").fill("调整部门对应能力")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const departmentCard = page
      .getByRole("region", { name: "设置改动确认卡" })
      .last()
    await expect(departmentCard).toContainText(
      "人事部：新增 新人带教；移除 盘点作业",
      { timeout: 30000 },
    )
    await expect(departmentCard).not.toContainText("new_skill")
    await expect(departmentCard).not.toContainText("old_skill")
    await expect(departmentCard).not.toContainText("运营部")
    await departmentCard
      .getByRole("button", { name: "确认应用", exact: true })
      .click()
    await expect(departmentCard.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(departmentPath, { headers })).json())
        .capability_keys,
    ).toEqual(["new_skill"])
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})
