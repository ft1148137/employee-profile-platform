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
    <div className="space-y-3 border-t pt-5">
      <Label htmlFor="drafting-note">起草补充说明</Label>
      <p className="text-sm text-muted-foreground">
        说明本企业工单和能力的业务含义，不能改变工具、权限、输出格式或起草规则。清空后保存即可停用。
      </p>
      <DraftField changed={suggestion.changed("text")}>
        <textarea
          className="min-h-32 w-full rounded-md border bg-background p-3 text-sm"
          id="drafting-note"
          rows={5}
          value={text}
          disabled={save.isPending || suggestion.blocked}
          onChange={(event) => setEdited(event.target.value)}
        />
      </DraftField>
      <p role="status" className="text-sm text-muted-foreground">
        {count} / 2000 字
      </p>
      <p className="text-sm text-muted-foreground">
        {note.data.version == null
          ? "尚未保存补充说明"
          : `当前版本：${note.data.version} · 最近修改：${note.data.created_by} · ${new Date(note.data.created_at ?? "").toLocaleString("zh-CN")}`}
      </p>
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      <SetupError error={save.error} />
      <Button
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
