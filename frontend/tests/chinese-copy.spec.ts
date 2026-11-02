import { expect, test } from "@playwright/test"
import { expectChineseCopy } from "./utils/copy"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })
const password = process.env.DEMO_USER_PASSWORD!

test("copy checker rejects English and ignores hidden content and allowed terms", async ({
  page,
}) => {
  await page.setContent(
    "<p>中文 Excel CSV HTTPS Jira PDF xlsx</p><p hidden>Hidden</p>",
  )
  await expectChineseCopy(page)
  await page.setContent("<p>Unexpected English</p>")
  await expect(expectChineseCopy(page)).rejects.toThrow(/Unexpected.*English/)
})

test("login and not-found copy is Chinese", async ({ page }) => {
  await page.goto("/login")
  await expect(page.getByRole("button", { name: "登录" })).toBeVisible()
  await expectChineseCopy(page)
  await page.goto("/unknown-page")
  await expect(page.getByTestId("not-found")).toBeVisible()
  await expect(page).toHaveTitle("页面不存在 - 员工画像平台")
  await expectChineseCopy(page)
})

for (const account of ["admin", "manager.platform", "employee.platform"]) {
  test(`${account}: navigation, settings, employees and profile use Chinese copy`, async ({
    page,
    request,
  }) => {
    await logInUser(page, `${account}@demo.example.com`, password)
    await expect(page.getByRole("heading").first()).toBeVisible()
    if (account !== "employee.platform") {
      await page.goto("/employees")
      await expect(page.getByText("共", { exact: false }).first()).toBeVisible()
      await expectChineseCopy(page)
      await page
        .getByRole("row")
        .filter({ hasText: "张伟" })
        .filter({ hasText: "平台研发部" })
        .click()
    }
    await expect(
      page.getByRole("heading", { name: "张伟", exact: true }),
    ).toBeVisible()
    await expectChineseCopy(page)
    await page.getByTestId("user-menu").click()
    await expect(page.getByRole("menuitem", { name: "退出登录" })).toBeVisible()
    await expectChineseCopy(page)
    await page.keyboard.press("Escape")
    await page.goto("/settings")
    await expect(page.getByRole("tabpanel")).toContainText("登录名")
    const login = await request.post("/api/v1/login/access-token", {
      form: { username: `${account}@demo.example.com`, password },
    })
    const { access_token } = await login.json()
    const me = await request.get("/api/v1/users/me", {
      headers: { Authorization: `Bearer ${access_token}` },
    })
    const { username } = await me.json()
    await expectChineseCopy(page, [username])
    await page.getByRole("tab", { name: "修改密码" }).click()
    await page.getByRole("button", { name: "保存密码" }).click()
    await expect(page.getByText("请输入密码").first()).toBeVisible()
    await expectChineseCopy(page)
    await page.getByRole("tab", { name: "外观", exact: true }).click()
    await page.getByRole("tabpanel").getByTestId("theme-button").click()
    await expect(page.getByRole("menuitem", { name: "跟随系统" })).toBeVisible()
    await expectChineseCopy(page)
  })
}

for (const [account, own] of [
  ["employee.platform", true],
  ["admin", false],
] as const) {
  test(`${account}: empty profile explains next steps and missing dictionary`, async ({
    page,
  }) => {
    await page.route("**/employees/*/profile", async (route) => {
      const response = await route.fetch()
      const profile = await response.json()
      await route.fulfill({
        json: {
          ...profile,
          employee: {
            ...profile.employee,
            latest_published: null,
            progress: null,
          },
          items: [],
          schema_version: null,
          latest_published: null,
          counts: { confirmed: 0, tentative: 0, unknown: 0, disputed: 0 },
        },
      })
    })
    await logInUser(page, `${account}@demo.example.com`, password)
    if (!own) await page.goto("/employees")
    if (!own)
      await page
        .getByRole("row")
        .filter({ hasText: "张伟" })
        .filter({ hasText: "平台研发部" })
        .click()
    await expect(
      page.getByRole("heading", {
        name: own ? "你的画像还没有内容" : "该员工的画像还没有内容",
      }),
    ).toBeVisible()
    await expect(page.getByText("企业管理员还没有选择能力词典")).toBeVisible()
    await expect(
      page.getByText(
        "画像只用来帮助跨部门找到合适的人，不用于绩效、薪酬和晋升。",
      ),
    ).toBeVisible()
    await expectChineseCopy(page)
    await page.screenshot({
      path: `test-results/empty-profile-${account}.png`,
      fullPage: true,
    })
  })
}

test("new employee logs in to a real empty profile without a dictionary", async ({
  page,
}) => {
  const email = randomEmail()
  const password = randomPassword()
  await createUser({ email, password, withEmptyProfile: true })
  await logInUser(page, email, password)
  await expect(page).toHaveURL(/\/employees\/[^/]+$/)
  await expect(
    page.getByRole("heading", { name: "你的画像还没有内容" }),
  ).toBeVisible()
  await expect(page.getByText("企业管理员还没有选择能力词典")).toBeVisible()
  await expect(page.getByText("未发布", { exact: true })).toBeVisible()
  await expectChineseCopy(page)
  await page.screenshot({
    path: "test-results/new-employee-empty-profile.png",
    fullPage: true,
  })
})
