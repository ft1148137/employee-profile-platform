import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { DraftingSettingsService } from "@/client"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"

export function DraftingNoteForm({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [edited, setEdited] = useState<string>()
  const resetManual = useCallback(() => setEdited(undefined), [])
  const suggestion = usePageDraft(orgId, "update_drafting_note", resetManual)
  const note = useQuery({
    queryKey: ["drafting-note", orgId],
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetDraftingNote({
          path: { org_id: orgId },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (text: string) =>
      DraftingSettingsService.settingsPutDraftingNote({
        path: { org_id: orgId },
        body: { text },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["drafting-note", orgId], data)
      setEdited(undefined)
    },
  })
  if (note.isPending) return <ProfileLoading />
  if (note.isError) return <ProfileError retry={() => note.refetch()} />
  const text = suggestion.values({ text: edited ?? note.data.text }).text
  const count = Array.from(text).length
  return (
    <div className="min-w-0 space-y-2 border-t border-surface-border pt-3">
      <Label htmlFor="drafting-note">起草补充说明</Label>
      <p className="text-sm text-muted-foreground">
        可补充本企业工单与业务背景说明（不影响权限和起草规则；清空保存即停用）。
      </p>
      <DraftField changed={suggestion.changed("text")}>
        <textarea
          className="min-h-20 w-full rounded-md border bg-background p-2 text-sm"
          id="drafting-note"
          rows={3}
          value={text}
          disabled={save.isPending || suggestion.blocked}
          onChange={(event) => setEdited(event.target.value)}
        />
      </DraftField>
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <p className="min-w-0 break-words">
          {note.data.version == null
            ? "尚未保存补充说明"
            : `当前版本：${note.data.version} · 最近修改：${note.data.created_by} · ${new Date(note.data.created_at ?? "").toLocaleString("zh-CN")}`}
        </p>
        <p role="status" className="tabular-nums">
          {count} / 2000 字
        </p>
      </div>
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      <SetupError error={save.error} />
      <Button
        size="sm"
        disabled={
          save.isPending ||
          suggestion.blocked ||
          count > 2000 ||
          text === note.data.text
        }
        onClick={() => save.mutate(text)}
      >
        {save.isPending ? "正在保存…" : "保存补充说明"}
      </Button>
      {save.isSuccess && edited === undefined && (
        <p role="status">补充说明已保存</p>
      )}
    </div>
  )
}
