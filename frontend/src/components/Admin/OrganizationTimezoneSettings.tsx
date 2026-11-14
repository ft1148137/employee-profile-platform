import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { DraftingSettingsService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { handleError } from "@/utils"

const timezones = [
  ["Asia/Shanghai", "北京时间"],
  ["Asia/Hong_Kong", "香港时间"],
  ["Asia/Singapore", "新加坡时间"],
  ["Asia/Tokyo", "东京时间"],
  ["Asia/Kolkata", "印度时间"],
  ["Asia/Dubai", "迪拜时间"],
  ["Europe/London", "伦敦时间"],
  ["Europe/Berlin", "柏林时间"],
  ["America/New_York", "纽约时间"],
  ["America/Los_Angeles", "洛杉矶时间"],
  ["Pacific/Auckland", "奥克兰时间"],
  ["UTC", "协调世界时"],
]

export function OrganizationTimezoneSettings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const queryKey = ["org-timezone", orgId]
  const settings = useQuery({
    queryKey,
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetOrgTimezone({
          path: { org_id: orgId },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (timezone: string) =>
      DraftingSettingsService.settingsPutOrgTimezone({
        path: { org_id: orgId },
        body: { timezone },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(queryKey, data)
      setSelected(null)
      setMessage("企业时区已保存。")
    },
    onError: (error) => handleError.call(setMessage, error),
  })
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const timezone = selected ?? settings.data.timezone
  return (
    <section className="space-y-4 border-t pt-4" aria-label="企业时区设置">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate(timezone)
        }}
      >
        <Label htmlFor="org-timezone">企业时区</Label>
        <Select
          value={timezone}
          disabled={save.isPending}
          onValueChange={(value) => {
            setSelected(value)
            setMessage("")
          }}
        >
          <SelectTrigger id="org-timezone" className="w-full sm:max-w-md">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {timezones.map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}（{value}）
              </SelectItem>
            ))}
            {!timezones.some(([value]) => value === timezone) && (
              <SelectItem value={timezone}>{timezone}</SelectItem>
            )}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          用于直接修改画像的通知时间、没有时区的工作记录导入，以及当地上午 9
          点的管理员每日待办。
        </p>
        {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
        <Button
          type="submit"
          disabled={save.isPending || timezone === settings.data.timezone}
        >
          {save.isPending ? "正在保存…" : "保存企业时区"}
        </Button>
      </form>
    </section>
  )
}
