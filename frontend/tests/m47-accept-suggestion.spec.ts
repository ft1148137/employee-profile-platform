import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("admin accepts suggestions without entering identifiers or level descriptions", async ({
  page,
  request,
}, testInfo) => {
  const email = `m47-suggestion-${crypto.randomUUID()}@example.com`
  const password = "synthetic-password-2026"
  const token = await request.post("/api/v1/private/setup-tokens", {
    data: { purpose: "new_org" },
  })
  expect(token.ok()).toBeTruthy()
  const initialized = await request.post("/api/v1/setup/initialize", {
    data: {
      token: (await token.json()).token,
      org_name: "接纳建议合成企业",
      admin_username: email,
      admin_password: password,
    },
  })
  expect(initialized.ok()).toBeTruthy()
  const { access_token, org_id } = await initialized.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const base = `/api/v1/orgs/${org_id}/capabilities`
  const levels = [
    {
      key: "needs_support",
      label: "需要指导",
      description: "在指导下完成任务",
    },
    { key: "routine", label: "常规任务", description: "能完成常规任务" },
    {
      key: "independent_complex",
      label: "复杂任务",
      description: "能独立完成复杂任务",
    },
  ]
  expect(
    (
      await request.put(base, {
        headers,
        data: {
          expected_version: 0,
          definitions: [
            {
              key: "capability_1",
              name: "已有能力",
              category: "工程",
              description: "合成说明",
              level_rubric: { levels },
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy()
  for (const name of ["新合成能力", "另一项合成能力"]) {
    expect(
      (
        await request.post(
          `/api/v1/private/capability-proposals?org_id=${org_id}&name=${encodeURIComponent(name)}`,
        )
      ).ok(),
    ).toBeTruthy()
  }
  await logInUser(page, email, password)
  await page.goto("/admin/rules?tab=dictionary#dictionary-editor")
  const editor = page.getByRole("region", { name: "网页编辑能力词典" })
  await editor.getByRole("button", { name: "新增能力", exact: true }).click()
  const manual = editor.getByRole("article", { name: "新增能力", exact: true })
  await expect(manual.getByLabel("能力标识")).toHaveValue("capability_2")
  await manual.getByLabel("等级描述").fill("在指导下完成")
  await manual.getByLabel("能力名称").fill("手工新增合成能力")
  for (const name of ["新合成能力", "另一项合成能力"]) {
    await editor
      .getByRole("article", { name: `建议：${name}`, exact: true })
      .getByRole("button", { name: "加入词典", exact: true })
      .click()
    const added = editor.getByRole("article", { name, exact: true })
    await expect(added.getByLabel("能力标识")).toHaveCount(0)
    await expect(added.getByLabel("能力说明")).toHaveValue("合成能力说明")
    await expect(added.getByLabel("等级描述")).toHaveCount(3)
    for (const [index, level] of levels.entries()) {
      await expect(added.getByLabel("等级描述").nth(index)).toHaveValue(
        level.description,
      )
      await expect(added.getByLabel("等级描述").nth(index)).toBeEditable()
    }
  }
  await page.screenshot({
    path: testInfo.outputPath("accept-suggestion.png"),
    fullPage: true,
  })
  await editor.getByRole("button", { name: "预览改动", exact: true }).click()
  await editor
    .getByRole("button", { name: "确认保存新版本", exact: true })
    .click()
  await expect(
    page.getByRole("status").filter({ hasText: "新词典已启用" }),
  ).toBeVisible()
  const dictionary = await request.get(base, { headers })
  expect(dictionary.ok()).toBeTruthy()
  const saved = (await dictionary.json()).data
  expect(saved.map((item: { key: string }) => item.key)).toEqual([
    "capability_1",
    "capability_2",
    "capability_3",
    "capability_4",
  ])
  for (const name of ["新合成能力", "另一项合成能力"]) {
    expect(
      saved.find((item: { name: string }) => item.name === name).level_rubric
        .levels,
    ).toEqual(levels)
  }
  expect(
    (await (await request.get(`${base}/proposals`, { headers })).json()).data,
  ).toEqual([])
  await page.reload()
  await expect(
    editor.getByRole("article", { name: "新合成能力", exact: true }),
  ).toBeVisible()
  await expect(
    editor.getByRole("region", { name: "建议新增的能力" }),
  ).toHaveCount(0)
})
