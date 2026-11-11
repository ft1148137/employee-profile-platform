import type { Page } from "@playwright/test"
import { expect, test } from "@playwright/test"
import type { AttachmentPublic } from "../src/client"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZusAAAAASUVORK5CYII=",
  "base64",
)
const file = {
  name: "合成经历.txt",
  mimeType: "text/plain",
  buffer: Buffer.from("完成合成系统测试"),
}
const image = { name: "合成图片.png", mimeType: "image/png", buffer: png }

test.use({ storageState: { cookies: [], origins: [] } })

async function openChat(page: Page, bot = false) {
  const uploads: Promise<AttachmentPublic>[] = []
  const turns: { text: string; attachment_ids: string[] }[] = []
  const saved = new Map<string, object[]>()
  const channel = bot ? "bot" : "admin-assistant"
  page.on("response", (response) => {
    if (
      response.request().method() === "POST" &&
      response.url().includes(`/${channel}/sessions/`) &&
      response.url().split("?")[0].endsWith("/attachments") &&
      response.ok()
    )
      uploads.push(response.json())
  })
  await page.route(`**/${channel}/sessions/*/messages?*`, (route) =>
    route.fulfill({
      json:
        saved.get(route.request().url().split("/sessions/")[1].split("/")[0]) ??
        [],
    }),
  )
  await page.route(`**/${channel}/sessions/*/turns?*`, async (route) => {
    const body = route.request().postDataJSON()
    turns.push(body)
    const attachments = (await Promise.all(uploads)).filter((item) =>
      body.attachment_ids?.includes(item.id),
    )
    const response = {
      messages: [
        { type: "text", text: "当前模型不支持图片，图片没有发送给模型" },
      ],
    }
    saved.set(route.request().url().split("/sessions/")[1].split("/")[0], [
      { role: "user", payload: { ...body, attachments } },
      { role: "assistant", payload: response },
    ])
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "final", response })}\n`,
    })
  })
  if (bot) {
    const email = `attachments-${crypto.randomUUID()}@example.com`
    const password = "synthetic-password-2026"
    await createUser({ email, password, withEmptyProfile: true })
    await logInUser(page, email, password)
    await page.getByRole("button", { name: "打开画像机器人" }).click()
  } else {
    await page.route("**/admin-assistant/availability?*", (route) =>
      route.fulfill({ json: { available: true, reason: "" } }),
    )
    await logInUser(
      page,
      "admin@demo.example.com",
      process.env.DEMO_USER_PASSWORD!,
    )
    await page.goto("/admin/assistant")
    await page.getByRole("link", { name: "新对话", exact: true }).click()
  }
  const input = page.getByLabel(bot ? "发给机器人的消息" : "发给管理助手的消息")
  await expect(input).toBeEnabled()
  return { input, uploads, turns }
}

async function upload(page: Page, files: typeof file | (typeof file)[]) {
  const uploaded = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().split("?")[0].endsWith("/attachments"),
  )
  await page.getByLabel("选择对话附件").setInputFiles(files)
  const response = await uploaded
  expect(response.ok()).toBeTruthy()
  await expect(page.getByRole("list", { name: "待发送附件" })).toContainText(
    "已上传",
  )
}

for (const method of ["button", "drop", "paste"] as const) {
  test(`admin uploads through ${method}, sends, displays and downloads attachments`, async ({
    page,
  }) => {
    const { input, turns } = await openChat(page)
    if (method === "button") {
      const chooser = page.waitForEvent("filechooser")
      await page.getByRole("button", { name: "添加附件" }).click()
      await (await chooser).setFiles([file, image])
    } else {
      await input.evaluate(
        (element, { method, png }) => {
          const data = new DataTransfer()
          data.items.add(
            new File(["完成合成系统测试"], "合成经历.txt", {
              type: "text/plain",
            }),
          )
          data.items.add(
            new File([new Uint8Array(png)], "合成图片.png", {
              type: "image/png",
            }),
          )
          element.dispatchEvent(
            method === "drop"
              ? new DragEvent("drop", {
                  bubbles: true,
                  cancelable: true,
                  dataTransfer: data,
                })
              : new ClipboardEvent("paste", {
                  bubbles: true,
                  cancelable: true,
                  clipboardData: data,
                }),
          )
        },
        { method, png: Array.from(png) },
      )
    }
    const pending = page.getByRole("list", { name: "待发送附件" })
    await expect(pending.getByText("已上传", { exact: true })).toHaveCount(2)
    await input.fill("请查看合成附件")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const log = page.getByRole("log")
    await expect(log.getByLabel(`附件：${file.name}`)).toBeVisible()
    const thumbnail = log.getByRole("img", { name: image.name })
    await expect(thumbnail).toBeVisible()
    await expect(thumbnail).toHaveJSProperty("naturalWidth", 1)
    await expect(thumbnail).toHaveAttribute("src", /^blob:/)
    await expect(log).toContainText("当前模型不支持图片，图片没有发送给模型")
    await expect(pending.getByRole("listitem")).toHaveCount(0)
    expect(turns[0].attachment_ids).toHaveLength(2)
    const download = page.waitForEvent("download")
    await log
      .getByLabel(`附件：${file.name}`)
      .getByRole("button", { name: "下载附件" })
      .click()
    expect((await download).suggestedFilename()).toBe(file.name)
    const popup = page.waitForEvent("popup")
    await thumbnail.click()
    expect((await popup).url()).toMatch(/^blob:/)
    if (method === "button") {
      await page.reload()
      await expect(
        page.getByRole("log").getByLabel(`附件：${file.name}`),
      ).toBeVisible()
      await expect(
        page.getByRole("log").getByRole("img", { name: image.name }),
      ).toBeVisible()
    }
  })
}

test("bot uploads a file, removes an attachment, sends and displays the card", async ({
  page,
}) => {
  const { turns } = await openChat(page, true)
  await upload(page, file)
  await page.getByRole("button", { name: `移除 ${file.name}` }).click()
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled()
  await upload(page, file)
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByRole("log").getByLabel(`附件：${file.name}`),
  ).toBeVisible()
  expect(turns[0].attachment_ids).toHaveLength(1)
})

test("invalid counts, types and sizes stay unsent; extraction failures block sending", async ({
  page,
}) => {
  const { input, turns } = await openChat(page)
  let requests = 0
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().split("?")[0].endsWith("/attachments")
    )
      requests++
  })
  const picker = page.getByLabel("选择对话附件")
  await picker.setInputFiles(
    Array.from({ length: 6 }, (_, index) => ({
      ...file,
      name: `${index}.txt`,
    })),
  )
  await expect(page.getByRole("alert")).toContainText("每轮最多 5 个附件")
  await picker.setInputFiles({ ...file, name: "不能上传.exe" })
  await expect(page.getByRole("alert")).toContainText("不支持的附件类型")
  for (const oversize of [
    { ...image, buffer: Buffer.alloc(5 * 1024 * 1024 + 1) },
    { ...file, buffer: Buffer.alloc(10 * 1024 * 1024 + 1) },
  ]) {
    await picker.setInputFiles(oversize)
    await expect(page.getByRole("alert")).toContainText(
      "附件不能为空，图片最多 5 MiB，文件最多 10 MiB",
    )
  }
  expect(requests).toBe(0)
  expect(turns).toHaveLength(0)
  await input.fill("上传失败时不能发送")
  await picker.setInputFiles({ ...file, name: "损坏.xlsx" })
  await expect(page.getByRole("list", { name: "待发送附件" })).toContainText(
    "文件无法读取",
  )
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled()
  await input.press("Enter")
  expect(turns).toHaveLength(0)
})

test("shows truncation notices and a cleaned attachment without retrying", async ({
  page,
}) => {
  await openChat(page)
  await upload(page, { ...file, buffer: Buffer.from("合".repeat(20001)) })
  await expect(page.getByRole("list", { name: "待发送附件" })).toContainText(
    "只读了前 20,000 字",
  )
  let downloads = 0
  await page.route("**/admin-assistant/sessions/*/attachments/*?*", (route) => {
    downloads++
    return route.fulfill({ status: 404, json: { detail: "附件不存在" } })
  })
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const card = page.getByRole("log").getByLabel(`附件：${file.name}`)
  await expect(card).toContainText("只读了前 20,000 字")
  await card.getByRole("button", { name: "下载附件" }).click()
  await expect(card).toContainText("已按保留期清理")
  await expect(card.getByRole("button")).toHaveCount(0)
  expect(downloads).toBe(1)
})

test("keeps attachments on stop and sends them again", async ({ page }) => {
  const { input, turns } = await openChat(page)
  await upload(page, file)
  await page.evaluate(() => {
    const original = window.fetch
    let first = true
    window.fetch = async (input, init) => {
      if (typeof input !== "string" || !input.includes("/turns?") || !first)
        return original(input, init)
      first = false
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                `${JSON.stringify({ type: "text", text: "可停止的合成回复" })}\n`,
              ),
            )
            init?.signal?.addEventListener(
              "abort",
              () => controller.error(new DOMException("Stopped", "AbortError")),
              { once: true },
            )
          },
        }),
        { headers: { "Content-Type": "application/x-ndjson" } },
      )
    }
  })
  await input.fill("停止后重发")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByText("可停止的合成回复", { exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "停止", exact: true }).click()
  await expect(page.getByText("已停止", { exact: true })).toBeVisible()
  await expect(page.getByRole("list", { name: "待发送附件" })).toContainText(
    file.name,
  )
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByRole("log").getByLabel(`附件：${file.name}`),
  ).toBeVisible()
  expect(turns[0].attachment_ids).toHaveLength(1)
})
