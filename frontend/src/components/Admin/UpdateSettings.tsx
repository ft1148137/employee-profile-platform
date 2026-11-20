import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { DraftingSettingsService, type UpdateSettingsInput } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import {
  OrganizationTimezoneSettings,
  timezoneLabel,
  useOrganizationTimezone,
} from "@/components/Admin/OrganizationTimezoneSettings"
import { RulesLayout } from "@/components/Admin/RulesLayout"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
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

const schedules = {
  quarterly: "每季度",
  yearly: "每年",
  off: "关闭",
} as const

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
  const timezone = useOrganizationTimezone(orgId)
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
    <RulesLayout
      summary={[
        ["提醒周期", schedules[settings.data.reminder_schedule ?? "quarterly"]],
        ["证据过期期限", `${settings.data.stale_after_months ?? 24} 个月`],
        [
          "企业时区",
          timezone.data ? timezoneLabel(timezone.data.timezone) : "—",
        ],
      ]}
      history={<ChangeHistory targetType="update_setting" variant="compact" />}
    >
      <SectionCard className="space-y-3 p-3" aria-labelledby="updates-title">
        <div className="flex items-center gap-2">
          <h2 id="updates-title" className="text-sm font-semibold">
            定期更新提醒
          </h2>
          <HelpTip
            label="定期更新提醒说明"
            content="按周期在收件箱提醒员工核对近况。超期证据仅提示核对，等级保持原样。"
          />
        </div>
        <PageDraft
          orgId={orgId}
          draft={suggestion.draft}
          error={suggestion.error}
        />
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (suggestion.blocked) return
            save.mutate({
              reminder_schedule: current.reminder_schedule,
              stale_after_months: current.stale_after_months,
            })
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <DraftField changed={suggestion.changed("reminder_schedule")}>
              <Label htmlFor="reminder-schedule" className="mb-1.5">
                提醒周期
              </Label>
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
                <SelectTrigger
                  id="reminder-schedule"
                  size="sm"
                  className="w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(schedules).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </DraftField>
            <DraftField changed={suggestion.changed("stale_after_months")}>
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <Label htmlFor="stale-months">证据过期提醒（月）</Label>
                <HelpTip
                  label="证据过期提醒说明"
                  content="可设置 6–120 个月，默认 24 个月。"
                />
              </div>
              <Input
                id="stale-months"
                className="h-8"
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
            </DraftField>
          </div>
          {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
          <Button
            size="sm"
            disabled={save.isPending || suggestion.blocked || !changes}
            type="submit"
          >
            {save.isPending ? "正在保存…" : "保存提醒设置"}
          </Button>
        </form>
      </SectionCard>
      <OrganizationTimezoneSettings orgId={orgId} />
    </RulesLayout>
  )
}
