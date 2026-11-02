import { expect, type Page } from "@playwright/test"

const allowedWords = [
  "Excel",
  "CSV",
  "HTTP",
  "HTTPS",
  "Jira",
  "PDF",
  "xlsx",
  "API",
  "RKNN",
]

/** Ignore addresses and URLs (user data), and explicitly allow technical terms. */
export async function expectChineseCopy(
  page: Page,
  extraAllowedWords: string[] = [],
) {
  // biome-ignore lint/performance/useDomNodeTextContent: This check must exclude hidden text.
  const visibleText = await page.locator("body").innerText()
  const text = visibleText
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "")
    .replace(/https?:\/\/\S+/gi, "")
  const allowed = new Set(
    [...allowedWords, ...extraAllowedWords].flatMap(
      (word) => word.toLowerCase().match(/[a-z]{3,}/g) ?? [],
    ),
  )
  const unexpected = [...new Set(text.match(/[A-Za-z]{3,}/g) ?? [])].filter(
    (word) => !allowed.has(word.toLowerCase()),
  )
  expect(unexpected, `页面存在未翻译的英文：${unexpected.join("、")}`).toEqual(
    [],
  )
}
