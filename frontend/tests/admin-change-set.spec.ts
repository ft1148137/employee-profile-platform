import type { APIRequestContext } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { expect, test } from "./utils/model"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

async function createOrg(request: APIRequestContext, modelUrl: string) {
  const email = `admin-change-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "提醒设置合成企业",
      admin_username: email,
      admin_password: password,
      admin_full_name: "管理员",
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { org_id: orgId, access_token: accessToken } = await initialized.json()
  const base = `/api/v1/orgs/${orgId}`
  const headers = { Authorization: `Bearer ${accessToken}` }
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
  return { email, password, base, headers }
}

test("reset password requires two clicks and disappears after reload", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          `工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,reset-${crypto.randomUUID()}@example.com\n`,
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const preview = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (await request.post(`${base}/accounts/issue`, { headers, data: {} })).ok(),
  ).toBeTruthy()
  const { data: people } = await (
    await request.get(`${base}/accounts`, { headers })
  ).json()
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill(`重置密码 ${people[0].account.user_id}`)
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card.getByText("重置密码", { exact: true })).toBeVisible({
    timeout: 30000,
  })
  await expect(card.getByText(/涉及账号：合成员工/)).toBeVisible()
  await expect(card.getByText("修改前", { exact: true })).toHaveCount(0)
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveCount(0)
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/apply") &&
      response.request().method() === "POST",
  )
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  const response = await responsePromise
  expect(response.headers()["cache-control"]).toBe("no-store")
  const secret = (await response.json()).one_time[0].initial_password
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveValue(secret)
  await expect(card.getByRole("button", { name: "复制初始密码" })).toBeVisible()
  await expect(
    card.getByRole("button", { name: "撤销", exact: true }),
  ).toHaveCount(0)
  await expect(card).not.toContainText(people[0].account.user_id)
  await expectChineseCopy(page, [people[0].account.user_id])
  await page.reload()
  await expect(card.getByRole("status")).toHaveText("已应用")
  await expect(card.getByLabel("初始密码", { exact: true })).toHaveCount(0)
  await expect(page.getByText(secret, { exact: true })).toHaveCount(0)
})

for (const width of [1440, 390]) {
  test(`reminder change is previewed, applied and undone at ${width}px`, async ({
    page,
    request,
    modelUrl,
  }, testInfo) => {
    test.setTimeout(90000)
    const { email, password, base, headers } = await createOrg(
      request,
      modelUrl,
    )
    await page.setViewportSize({ width: 1440, height: 900 })
    await logInUser(page, email, password)
    await page.setViewportSize({ width, height: 900 })
    await page.goto("/admin/rules?tab=reminders")
    await page.getByRole("button", { name: "打开管理助手" }).click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await dialog.getByRole("button", { name: "发送", exact: true }).click()
    const card = dialog.getByRole("region", { name: "设置改动确认卡" })
    await expect(card).toBeVisible({ timeout: 30000 })
    await expect(card.getByText("每季度", { exact: true })).toBeVisible()
    await expect(card.getByText("每年", { exact: true })).toBeVisible()
    await expect(card.getByText("36 个月", { exact: true })).toBeVisible()
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await card.getByRole("button", { name: "确认应用" }).click()
    await expect(card.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("yearly")
    await page.screenshot({
      path: testInfo.outputPath(`change-applied-${width}.png`),
    })
    await card.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(card.getByRole("status")).toHaveText("已撤销")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
    await expectChineseCopy(page)
    await page.screenshot({
      path: testInfo.outputPath(`change-undone-${width}.png`),
    })
    await page.reload()
    await page.getByRole("button", { name: "打开管理助手" }).click()
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")
    await page.getByRole("link", { name: "全屏打开", exact: true }).click()
    await expect(page).toHaveURL("/admin/assistant")
    await expect(
      page.getByRole("region", { name: "设置改动确认卡" }).getByRole("status"),
    ).toHaveText("已撤销")

    await page
      .getByRole("textbox", { name: "发给管理助手的消息" })
      .fill("把提醒改成每年一次，证据期限改为36个月")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const latest = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await latest.getByRole("button", { name: "确认应用" }).click()
    await expect(latest.getByRole("status")).toHaveText("已应用")
    expect(
      (
        await request.put(`${base}/update-settings`, {
          headers,
          data: { reminder_schedule: "off", stale_after_months: 24 },
        })
      ).ok(),
    ).toBeTruthy()
    await latest.getByRole("button", { name: "撤销", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await page.reload()
    const reverse = page.getByRole("region", { name: "设置改动确认卡" }).last()
    await expect(
      reverse.getByRole("heading", { name: "撤销前请确认" }),
    ).toBeVisible()
    await expect(reverse.getByRole("status")).toHaveText("待确认")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("off")
    await reverse.getByRole("button", { name: "确认应用" }).click()
    await expect(reverse.getByRole("status")).toHaveText("已应用")
    expect(
      (await (await request.get(`${base}/update-settings`, { headers })).json())
        .reminder_schedule,
    ).toBe("quarterly")
  })
}

test("department drafting change shows affected people and can be undone", async ({
  page,
  request,
  modelUrl,
}) => {
  test.setTimeout(90000)
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  const imported = await request.post(`${base}/roster/imports`, {
    headers,
    multipart: {
      file: {
        name: "名单.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,synthetic@example.com\n",
        ),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const preview = await imported.json()
  expect(
    (
      await request.post(`${base}/roster/imports/${preview.id}/apply`, {
        headers,
        data: { base_revision: preview.base_revision },
      })
    ).ok(),
  ).toBeTruthy()
  const departments = await (
    await request.get(`${base}/departments`, { headers })
  ).json()
  const departmentId = departments.data[0].id
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill(`关闭部门自动起草 ${departmentId}`)
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card.getByText(/影响 1 人/)).toBeVisible({ timeout: 30000 })
  const read = async () =>
    (await (await request.get(`${base}/drafting-settings`, { headers })).json())
      .disabled_department_ids
  expect(await read()).toEqual([])
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(await read()).toEqual([departmentId])
  await card.getByRole("button", { name: "撤销", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已撤销")
  expect(await read()).toEqual([])
})

test("closing manager confirmation requires two clicks with impact", async ({
  page,
  request,
  modelUrl,
}) => {
  const { email, password, base, headers } = await createOrg(request, modelUrl)
  await logInUser(page, email, password)
  await page.goto("/admin/assistant")
  await page
    .getByRole("textbox", { name: "发给管理助手的消息" })
    .fill("关闭经理确认")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("region", { name: "设置改动确认卡" })
  await expect(card).toBeVisible({ timeout: 30000 })
  await card.getByRole("button", { name: "确认应用" }).click()
  await expect(card.getByRole("status")).toHaveText("请再次确认")
  await expect(card.getByRole("alert")).toContainText("影响全体在职员工")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(true)
  await page.reload()
  await expect(
    card.getByRole("button", { name: "再次确认", exact: true }),
  ).toBeVisible()
  await card.getByRole("button", { name: "再次确认", exact: true }).click()
  await expect(card.getByRole("status")).toHaveText("已应用")
  expect(
    (await (await request.get(`${base}/approval-policy`, { headers })).json())
      .current.require_manager,
  ).toBe(false)
  await expectChineseCopy(page)
})
