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
  await page.goto("/employees")
  await expect(
    page.getByText("导出本人可见的全部已发布判断，不受页面筛选影响。"),
  ).toBeVisible()
  await page
    .getByRole("textbox", { name: "搜索姓名" })
    .fill("没有匹配的合成员工")
  await expect(page.getByText("共 0 名员工")).toBeVisible()
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
  expect(lines[0]).toBe(
    "工号,能力,等级,判断状态,证据数,最近证据时间,批准人,批准时间",
  )
  expect(lines.slice(1).map((line) => line.split(",")[0])).toEqual(
    expected.map((row) => row.employee_code ?? ""),
  )
  const jsonDownload = page.waitForEvent("download")
  await page
    .getByRole("button", { name: "导出已发布画像（JSON）", exact: true })
    .click()
  const jsonFile = await jsonDownload
  expect(jsonFile.suggestedFilename()).toBe("已发布画像.json")
  const jsonBytes = await jsonFile.createReadStream().then(async (stream) => {
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(chunk)
    return Buffer.concat(chunks)
  })
  expect(JSON.parse(jsonBytes.toString("utf8"))).toEqual(jsonRows)
  await page.screenshot({
    path: test.info().outputPath("profile-export.png"),
    fullPage: true,
  })
})
