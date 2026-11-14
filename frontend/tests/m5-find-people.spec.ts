import { expect, test } from "@playwright/test"
import type { PeopleSearchRow } from "../src/client"
import { logInUser } from "./utils/user"

const password = process.env.DEMO_USER_PASSWORD!
test.use({ storageState: { cookies: [], origins: [] } })

test("manager filters by capability and minimum level and opens the profile", async ({
  page,
  request,
}) => {
  await logInUser(page, "manager.platform@demo.example.com", password)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const base = `/api/v1/orgs/${memberships[0].org.id}`
  const dictionary = await (
    await request.get(`${base}/capabilities`, { headers })
  ).json()
  const capability = dictionary.data[0]
  const employees = await (
    await request.get(`${base}/employees`, { headers })
  ).json()
  const employee = employees.data[0]
  // Freeze only search results; shared demo data and the profile route stay untouched.
  const row: PeopleSearchRow = {
    employee_id: employee.id,
    display_name: employee.display_name,
    employee_code: employee.employee_code,
    department_name: employee.department.name,
    capability_key: capability.key,
    capability_name: capability.name,
    statement: "合成判断：能独立完成复杂任务",
    level: "independent_complex",
    level_name: "能独立完成复杂任务",
    status: "confirmed",
    evidence_count: 2,
    latest_evidence_at: "2026-11-01T00:00:00Z",
    published_by: { actor_type: "user", display_name: "合成批准人" },
    published_at: "2026-11-02T00:00:00Z",
  }
  await page.route("**/people-search?*", (route) => {
    const query = new URL(route.request().url()).searchParams
    expect(query.get("capability_key")).toBe(capability.key)
    expect(query.get("minimum_level")).toBe("independent_complex")
    return route.fulfill({ json: { data: [row], count: 1 } })
  })
  await page.getByRole("link", { name: "找人", exact: true }).click()
  await page.getByRole("combobox", { name: "能力（必选）" }).click()
  await page.getByRole("option", { name: capability.name, exact: true }).click()
  await page.getByRole("combobox", { name: "最低等级" }).click()
  const highest = capability.level_rubric.levels.find(
    (l: { key: string }) => l.key === "independent_complex",
  )
  await page.getByRole("option", { name: highest.label, exact: true }).click()
  await page.getByRole("button", { name: "查找", exact: true }).click()
  const results = page.getByRole("region", { name: "找人结果" })
  await expect(results).toContainText(row.statement)
  await expect(results).toContainText("合成批准人")
  await page.screenshot({
    path: test.info().outputPath("find-people.png"),
    fullPage: true,
  })
  await results
    .getByRole("link", { name: employee.display_name, exact: true })
    .click()
  await expect(page).toHaveURL(new RegExp(`/employees/${employee.id}$`))
  await expect(
    page.getByRole("heading", { name: employee.display_name, exact: true }),
  ).toBeVisible()
})

test("employee has no navigation entry and sees a forbidden message on direct access", async ({
  page,
}) => {
  await logInUser(page, "employee.platform@demo.example.com", password)
  await expect(
    page.getByRole("link", { name: "找人", exact: true }),
  ).toHaveCount(0)
  await page.goto("/find-people")
  await expect(page.getByRole("alert")).toContainText("没有权限使用找人")
  await expect(
    page.getByRole("combobox", { name: "能力（必选）" }),
  ).toHaveCount(0)
})
