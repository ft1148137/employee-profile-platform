import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { setupPlans } from "./utils/plans"

test.use({ storageState: { cookies: [], origins: [] } })

test("B 方经理快捷换人并锁定安排", async ({ page, request }) => {
  test.setTimeout(120000)
  const { person, m, start, later, demand, open } = await setupPlans(
    page,
    request,
    true,
  )
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/planning/demands/${demand.id}/cases`) &&
      response.request().method() === "POST",
  )
  await open(m)
  await expect(page.getByRole("button", { name: "关闭方案" })).toBeVisible()
  const created = await (await createdResponse).json()
  const direct = created.options.find(
    (option: { kind: string }) => option.kind === "direct",
  )
  expect(direct.verdict).toBe("infeasible")
  const card = page.getByRole("article", { name: "直接安排方案" })
  await card.getByRole("button", { name: "换人" }).click()
  await card.getByLabel("修改人员").selectOption(person("B1").id)
  await card.getByLabel("换成谁").selectOption(person("B3").id)
  await card.getByRole("button", { name: "确认修改" }).click()
  const revised = page.getByRole("article", { name: "自定方案" }).last()
  await expect(revised).toContainText("在「直接安排方案」上修改")
  await expect(revised).toContainText("把合成研发甲换成合成研发丙")
  await expect(revised).toContainText("这次改了什么")
  await expect(revised).toContainText("结论：不可行")
  const weekText = (date: string) =>
    `${Number(date.slice(5, 7))} 月 ${Number(date.slice(8, 10))} 日那一周`
  await expect
    .soft(revised.getByText(/^解决风险：/))
    .toContainText(`合成研发甲：该周超出容量 10% · ${weekText(start)}`)
  await revised.getByRole("button", { name: "锁定", exact: true }).click()
  await revised.getByLabel("修改人员").selectOption(person("B3").id)
  await revised.getByRole("button", { name: "确认修改" }).click()
  const locked = page.getByRole("article", { name: "自定方案" }).last()
  await expect(locked).toContainText("合成研发丙 · 投入 50%")
  await expect(locked).toContainText("已锁定")
  await locked.getByRole("button", { name: "换人" }).click()
  await expect(
    locked.getByLabel("修改人员").locator(`option[value="${person("B3").id}"]`),
  ).toHaveCount(0)
  await locked.getByRole("button", { name: "取消", exact: true }).click()
  await locked.getByRole("button", { name: "锁定", exact: true }).click()
  await locked.getByLabel("修改人员").selectOption(person("B2").id)
  await locked.getByRole("button", { name: "确认修改" }).click()
  await expect(locked.getByText(/已锁定/)).toHaveCount(2)
  for (const name of ["改时间", "降低投入"]) {
    await expect.soft(locked.getByRole("button", { name })).toBeDisabled()
  }
  await expect.soft(locked).toContainText("请先解锁")

  await page.route("**/planning/cases/*/options/*/revise", (route) =>
    route.fulfill({
      status: 422,
      json: {
        detail: [
          { msg: "投入比例必须在允许范围内" },
          { msg: "请检查修改内容" },
        ],
      },
    }),
  )
  await card.getByRole("button", { name: "降低投入" }).click()
  await card.getByRole("button", { name: "确认修改" }).click()
  await expect
    .soft(card.getByRole("alert"))
    .toHaveText("投入比例必须在允许范围内；请检查修改内容")
  await page.unroute("**/planning/cases/*/options/*/revise")
  await card.getByLabel("新的投入比例").fill("5")
  await card.getByRole("button", { name: "确认修改" }).click()
  await expect(card.getByRole("alert")).toHaveText(
    "投入比例须为 10 至 100 的整数",
  )
  await expectChineseCopy(card)
  await card.getByLabel("新的投入比例").fill("80")
  await card.getByRole("button", { name: "确认修改" }).click()
  const increased = page.getByRole("article", { name: "自定方案" }).last()
  await expect(increased).toContainText("投入从50%改到80%")
  await expect
    .soft(increased.getByText(/^新增风险：/))
    .toContainText(`合成研发甲：该周超出容量 20% · ${weekText(later(7))}`)
  const snapshots: string[] = []
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes(`/planning/demands/${demand.id}/cases`)
    )
      snapshots.push(request.url())
  })
  await page.reload()
  await expect(page.getByRole("article").first()).toBeVisible()
  await expect
    .soft(page.getByRole("article", { name: "自定方案" }).last())
    .toContainText("投入从50%改到80%")
  expect.soft(snapshots).toHaveLength(0)
  await expectChineseCopy(page)
  expect(await page.locator("body").textContent()).not.toMatch(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )
})
