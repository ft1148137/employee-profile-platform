import { expect, test } from "@playwright/test"
import { setupPlans } from "./utils/plans"

test.use({ storageState: { cookies: [], origins: [] } })

test("需求方选择拒绝原因并在决策记录查看", async ({ page, request }) => {
  test.setTimeout(120_000)
  const s = await setupPlans(page, request)
  const path = `${s.base}/capacity/demands/${s.demand.id}`
  const offer = async (revision: number) => {
    const plan = await s.api(
      `${s.base}/planning/demands/${s.demand.id}/cases`,
      {},
      s.m,
    )
    const option = await s.api(
      `${s.base}/planning/cases/${plan.id}/options`,
      {
        assignees: ["B1", "B2"].map((code) => ({
          employee_id: s.person(code).id,
          start_date: s.start,
          end_date: s.later(13),
          allocation_percent: code === "B1" ? 40 : 50,
        })),
      },
      s.m,
    )
    return s.api(
      `${path}/reply`,
      {
        option_id: option.id,
        revision,
        outcome: "partial",
        text: "合成部门条件",
        outside_basis: true,
      },
      s.m,
    )
  }
  await offer(s.demand.revision)
  await s.open(s.a)
  await page.goto(`/capacity?tab=outgoing&demand=${s.demand.id}`)
  await page.getByRole("button", { name: "拒绝方案", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByLabel("拒绝理由").fill("请调整开始日期")
  await expect(dialog.getByRole("button", { name: "确认拒绝" })).toBeDisabled()
  await dialog.getByLabel("拒绝原因").selectOption({ label: "时间不合适" })
  await dialog.getByRole("button", { name: "确认拒绝" }).click()
  const history = page.getByRole("region", { name: "决策记录" })
  await expect(history).toContainText("时间不合适")
  await expect(history).toContainText("请调整开始日期")
  await expect(history).not.toContainText("合成部门条件")

  const current = await s.api(path, undefined, s.a)
  const pending = await offer(current.revision)
  const expiry = Date.parse(pending.proposal.hold_expires_at)
  await page.clock.install()
  await page.reload()
  const proposal = page.getByRole("region", { name: "方案提议", exact: true })
  await expect(proposal).toContainText("等待需求方确认")
  const browserNow = await page.evaluate(() => Date.now())
  await page.clock.fastForward(expiry - browserNow + 1000)
  await expect(proposal).toContainText("已过期")
  await expect(proposal.getByRole("button", { name: "接受方案" })).toHaveCount(
    0,
  )
})
