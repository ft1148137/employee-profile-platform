import type { Page } from "@playwright/test"
import { expect } from "@playwright/test"

export async function importRoster(page: Page, content: Buffer) {
  const { token, orgId } = await page.evaluate(() => ({
    token: localStorage.getItem("access_token"),
    orgId: localStorage.getItem("current_org_id"),
  }))
  const base = `/api/v1/orgs/${orgId}/roster/imports`
  const headers = { Authorization: `Bearer ${token}` }
  const upload = await page.request.post(base, {
    headers,
    multipart: {
      file: { name: "synthetic.csv", mimeType: "text/csv", buffer: content },
    },
  })
  expect(upload.ok()).toBeTruthy()
  const preview = await upload.json()
  expect(preview.can_apply).toBeTruthy()
  const applied = await page.request.post(`${base}/${preview.id}/apply`, {
    headers,
    data: { base_revision: preview.base_revision },
  })
  expect(applied.ok()).toBeTruthy()
}
