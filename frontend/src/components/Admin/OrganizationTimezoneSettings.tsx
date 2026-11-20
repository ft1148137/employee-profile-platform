import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { DraftingSettingsService } from "@/client"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
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

export function timezoneLabel(timezone: string) {
  return timezones.find(([value]) => value === timezone)?.[1] ?? timezone
}

export function useOrganizationTimezone(orgId: string) {
  return useQuery({
    queryKey: ["org-timezone", orgId],
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetOrgTimezone({
          path: { org_id: orgId },
        })
      ).data,
  })
}

export function OrganizationTimezoneSettings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const settings = useOrganizationTimezone(orgId)
  const save = useMutation({
    mutationFn: (timezone: string) =>
      DraftingSettingsService.settingsPutOrgTimezone({
        path: { org_id: orgId },
        body: { timezone },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["org-timezone", orgId], data)
      setSelected(null)
      setMessage("企业时区已保存。")
    },
    onError: (error) => handleError.call(setMessage, error),
  })
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const timezone = selected ?? settings.data.timezone
  return (
    <SectionCard className="space-y-3 p-3" aria-label="企业时区设置">
      <div className="flex items-center gap-2">
        <Label htmlFor="org-timezone" className="font-semibold">
          企业时区
        </Label>
        <HelpTip
          label="企业时区说明"
          content="用于通知时间显示、工作记录导入及每日 9:00 管理员待办推送。"
        />
      </div>
      <Select
        value={timezone}
        disabled={save.isPending}
        onValueChange={(value) => {
          setSelected(value)
          setMessage("")
        }}
      >
        <SelectTrigger
          id="org-timezone"
          size="sm"
          title={timezone}
          className="w-full sm:max-w-md"
        >
          <SelectValue>{timezoneLabel(timezone)}</SelectValue>
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
      {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
      <Button
        type="button"
        size="sm"
        onClick={() => save.mutate(timezone)}
        disabled={save.isPending || timezone === settings.data.timezone}
      >
        {save.isPending ? "正在保存…" : "保存企业时区"}
      </Button>
    </SectionCard>
  )
}
