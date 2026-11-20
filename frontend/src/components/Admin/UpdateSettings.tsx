import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { DraftingSettingsService, type UpdateSettingsInput } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { OrganizationTimezoneSettings } from "@/components/Admin/OrganizationTimezoneSettings"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { handleError } from "@/utils"

export function UpdateSettings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [changes, setChanges] = useState<UpdateSettingsInput | null>(null)
  const suggestion = usePageDraft(
    orgId,
    "update_update_settings",
    useCallback(() => setChanges(null), []),
  )
  const [message, setMessage] = useState("")
  const settings = useQuery({
    queryKey: ["update-settings", orgId],
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetUpdateSettings({
          path: { org_id: orgId },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (body: UpdateSettingsInput) =>
      DraftingSettingsService.settingsPutUpdateSettings({
        path: { org_id: orgId },
        body,
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["update-settings", orgId], data)
      client.invalidateQueries({ queryKey: ["profile", orgId] })
      setChanges(null)
      setMessage("提醒设置已保存，证据期限立即生效。")
    },
    onError: (error) => handleError.call(setMessage, error),
  })
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const current = suggestion.values(changes ?? settings.data)
  return (
    <div className="space-y-4">
      <section
        className="rounded-xl border p-6 space-y-4"
        aria-labelledby="updates-title"
      >
        <h2 id="updates-title" className="text-lg font-semibold">
          定期更新提醒
        </h2>
        <p className="text-sm text-muted-foreground">
          按周期在收件箱提醒员工核对近况；超期证据仅作提示，不改变现有等级。
        </p>
        <PageDraft
          orgId={orgId}
          draft={suggestion.draft}
          error={suggestion.error}
        />
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (suggestion.blocked) return
            save.mutate({
              reminder_schedule: current.reminder_schedule,
              stale_after_months: current.stale_after_months,
            })
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <DraftField changed={suggestion.changed("reminder_schedule")}>
              <Label htmlFor="reminder-schedule">提醒周期</Label>
              <Select
                value={current.reminder_schedule}
                disabled={save.isPending || suggestion.blocked}
                onValueChange={(
                  value: NonNullable<UpdateSettingsInput["reminder_schedule"]>,
                ) => {
                  setChanges({ ...current, reminder_schedule: value })
                  setMessage("")
                }}
              >
                <SelectTrigger id="reminder-schedule">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="quarterly">每季度</SelectItem>
                  <SelectItem value="yearly">每年</SelectItem>
                  <SelectItem value="off">关闭</SelectItem>
                </SelectContent>
              </Select>
            </DraftField>
            <DraftField changed={suggestion.changed("stale_after_months")}>
              <Label htmlFor="stale-months">证据过期提醒（月）</Label>
              <Input
                id="stale-months"
                type="number"
                min={6}
                max={120}
                required
                value={current.stale_after_months}
                disabled={save.isPending || suggestion.blocked}
                onChange={(e) => {
                  setChanges({
                    ...current,
                    stale_after_months: Number(e.target.value),
                  })
                  setMessage("")
                }}
              />
              <p className="text-xs text-muted-foreground">
                6–120 个月（默认 24 个月），超期后仅提示核对，不会自动降级。
              </p>
            </DraftField>
          </div>
          {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
          <Button
            disabled={save.isPending || suggestion.blocked || !changes}
            type="submit"
          >
            {save.isPending ? "正在保存…" : "保存提醒设置"}
          </Button>
        </form>
        <ChangeHistory targetType="update_setting" />
      </section>
      <OrganizationTimezoneSettings orgId={orgId} />
    </div>
  )
}
