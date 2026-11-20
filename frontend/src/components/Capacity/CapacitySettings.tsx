import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService, type CapacitySettingsInput } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useOrg } from "@/hooks/useOrg"
import { useCapacitySettings } from "./shared"

export function CapacitySettings() {
  const { org } = useOrg()
  return org && <SettingsForm key={org.id} orgId={org.id} />
}

function SettingsForm({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const settings = useCapacitySettings(orgId)
  const [changes, setChanges] = useState<CapacitySettingsInput | null>(null)
  const save = useMutation({
    mutationFn: (body: CapacitySettingsInput) =>
      CapacityService.putCapacitySettings({ path: { org_id: orgId }, body }),
    onSuccess: ({ data }) => {
      client.setQueryData(["capacity-settings", orgId], data)
      client.removeQueries({ queryKey: ["capacity", orgId] })
      setChanges(null)
    },
  })
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const current = changes ?? settings.data
  return (
    <form
      aria-label="负荷与需求规则"
      className="max-w-xl space-y-5 rounded-xl border p-6"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate(current)
      }}
    >
      <p className="text-sm text-muted-foreground">
        关闭后隐藏「规划」入口与相关助手能力，已有记录保留，不影响画像。
      </p>
      <fieldset disabled={save.isPending} className="space-y-5">
        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={current.enabled}
            onChange={(event) =>
              setChanges({ ...current, enabled: event.target.checked })
            }
          />
          启用规划与负荷功能
        </label>
        <div className="space-y-2">
          <Label htmlFor="requester-policy">谁可以提需求</Label>
          <Select
            value={current.requester_policy}
            onValueChange={(value) =>
              setChanges({
                ...current,
                requester_policy:
                  value === "managers" ? "managers" : "everyone",
              })
            }
          >
            <SelectTrigger id="requester-policy">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="everyone">全体员工</SelectItem>
              <SelectItem value="managers">只有部门经理</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <SetupError error={save.error} />
        <Button type="submit" disabled={!changes}>
          {save.isPending ? "正在保存…" : "保存设置"}
        </Button>
      </fieldset>
      {save.isSuccess && !changes && (
        <p role="status">负荷与需求规则已保存。</p>
      )}
    </form>
  )
}
