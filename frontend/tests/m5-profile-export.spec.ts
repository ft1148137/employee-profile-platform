import { expect, test } from "@playwright/test"
import type { PeopleSearchRow, ProfileExportRow } from "../src/client"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

test("manager downloads every visible published judgment despite page filters", async ({
  page,
  request,
}) => {
  await logInUser(
    page,
    "manager.platform@demo.example.com",
    process.env.DEMO_USER_PASSWORD!,
  )
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const memberships = await (
    await request.get("/api/v1/me/memberships", { headers })
  ).json()
  const base = `/api/v1/orgs/${memberships[0].org.id}`
  const dictionary = await (
    await request.get(`${base}/capabilities`, { headers })
  ).json()
  const expected: PeopleSearchRow[] = []
  for (const capability of dictionary.data) {
    let skip = 0
    while (true) {
      const response = await request.get(`${base}/people-search`, {
        headers,
        params: { capability_key: capability.key, skip, limit: 100 },
      })
      expect(response.ok()).toBeTruthy()
      const result = await response.json()
      expected.push(...result.data)
      skip += result.data.length
      if (skip >= result.count) break
    }
  }
  expect(expected.length).toBeGreaterThan(0)
  const response = await request.get(`${base}/profile-export?format=json`, {
    headers,
  })
  expect(response.ok()).toBeTruthy()
  const jsonRows: ProfileExportRow[] = await response.json()
  expect(jsonRows).toHaveLength(expected.length)
  expect(jsonRows.map((r) => [r.employee_code, r.capability_key])).toEqual(
    expected.map((r) => [r.employee_code, r.capability_key]),
  )
  await page.goto("/find-people")
  await expect(
    page.getByText("导出本人可见的全部已发布判断，不受页面筛选影响。"),
  ).toBeVisible()
  await page.getByLabel("最近证据起始日期").fill("2099-01-01")
  const downloadPromise = page.waitForEvent("download")
  await page
    .getByRole("button", { name: "导出已发布画像（CSV）", exact: true })
    .click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe("已发布画像.csv")
  const stream = await download.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(chunk)
  const bytes = Buffer.concat(chunks)
  expect(bytes.subarray(0, 3)).toEqual(Buffer.from([0xef, 0xbb, 0xbf]))
  // This shared synthetic seed has single-line cells; CSV quoting is tested in pytest.
  expect(
    jsonRows.every((row) =>
      Object.values(row).every((value) => !/[\r\n]/.test(String(value))),
    ),
  ).toBeTruthy()
  const lines = bytes.subarray(3).toString("utf8").trimEnd().split("\r\n")
  expect(lines).toHaveLength(expected.length + 1)
  expect(lines[0]).toBe(Object.keys(jsonRows[0]).join(","))
  expect(lines.slice(1).map((line) => line.split(",")[0])).toEqual(
    expected.map((row) => row.employee_code ?? ""),
  )
  await page.screenshot({
    path: test.info().outputPath("profile-export.png"),
    fullPage: true,
  })
})
