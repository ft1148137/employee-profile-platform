import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { DraftingSettingsService, type UpdateSettingsInput } from "@/client"
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
  const current = changes ?? settings.data
  return (
    <section
      className="rounded-xl border p-6 space-y-4"
      aria-labelledby="updates-title"
    >
      <h2 id="updates-title" className="text-lg font-semibold">
        持续更新提醒
      </h2>
      <p className="text-sm text-muted-foreground">
        到期时在员工收件箱中询问一两个问题；回答只保存为本人自述。证据较旧只作提醒，判断状态保持不变。
      </p>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({
            reminder_schedule: current.reminder_schedule,
            stale_after_months: current.stale_after_months,
          })
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="reminder-schedule">提醒周期</Label>
            <Select
              value={current.reminder_schedule}
              disabled={save.isPending}
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
            <p className="text-xs text-muted-foreground">
              选择周期后，员工会定期收到更新画像的提醒；关闭后不再定期提醒。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="stale-months">证据较旧期限（月）</Label>
            <Input
              id="stale-months"
              type="number"
              min={6}
              max={120}
              required
              value={current.stale_after_months}
              disabled={save.isPending}
              onChange={(e) => {
                setChanges({
                  ...current,
                  stale_after_months: Number(e.target.value),
                })
                setMessage("")
              }}
            />
            <p className="text-xs text-muted-foreground">
              6–120 个月，默认 24
              个月。超过期限的证据会标为较旧，提醒核对，不会自动降低能力等级。
            </p>
          </div>
        </div>
        {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
        <Button disabled={save.isPending || !changes} type="submit">
          {save.isPending ? "正在保存…" : "保存提醒设置"}
        </Button>
      </form>
    </section>
  )
}
