import type { DictionaryPreview } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"

export function DictionaryPreviewPanel({
  preview,
  busy,
  redraft,
  onRedraft,
  onSave,
}: {
  preview: DictionaryPreview
  busy: boolean
  redraft: boolean
  onRedraft: (value: boolean) => void
  onSave: () => void
}) {
  return (
    <section className="space-y-4" aria-label="词典改动预览">
      <h3 className="font-semibold">
        保存为第 {preview.expected_version + 1} 版
      </h3>
      {(
        [
          ["新增", preview.added],
          ["修改", preview.modified],
          ["删除", preview.removed],
        ] as const
      ).map(([label, keys]) => (
        <p key={label}>
          {label} {keys.length} 项：{keys.join("、") || "无"}
        </p>
      ))}
      {preview.errors.length > 0 && (
        <ul role="alert" className="space-y-2 text-destructive">
          {preview.errors.map((message, index) => (
            <li key={`${index}:${message}`}>{message}</li>
          ))}
        </ul>
      )}
      {preview.removed.map((key) => (
        <p key={key} className="text-sm">
          {key}：已有 {preview.removed_claim_counts[key] ?? 0} 条判断保留，
          {preview.removed_item_counts[key] ?? 0} 条待提交改动项将移除。
        </p>
      ))}
      <p className="text-sm text-muted-foreground">
        删除的能力会移至画像的「已不在当前词典中的能力」。未提交请求会更新，被删除的项会移除；等级失效时员工需重新选择或清空。
      </p>
      <label className="flex items-start gap-3" htmlFor="dictionary-redraft">
        <Checkbox
          id="dictionary-redraft"
          checked={redraft}
          disabled={busy}
          onCheckedChange={(value) => onRedraft(value === true)}
        />
        <span>按新词典重新起草还没过目的初始画像</span>
      </label>
      <Button
        disabled={
          busy || preview.errors.length > 0 || !preview.definitions.length
        }
        onClick={onSave}
      >
        {busy ? "正在保存…" : "确认保存新版本"}
      </Button>
    </section>
  )
}
