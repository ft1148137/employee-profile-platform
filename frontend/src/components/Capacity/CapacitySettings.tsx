import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService, type CapacitySettingsInput } from "@/client"
import { RulesLayout } from "@/components/Admin/RulesLayout"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
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
    <RulesLayout
      summary={[
        ["规划与负荷", settings.data.enabled ? "已启用" : "已关闭"],
        [
          "需求发起权限",
          settings.data.requester_policy === "managers"
            ? "只有部门经理"
            : "全体员工",
        ],
      ]}
      history={
        <SectionCard aria-label="影响范围" className="space-y-2 p-3">
          <h2 className="text-sm font-semibold">影响范围</h2>
          <p className="text-sm text-muted-foreground">
            关闭后隐藏「规划」入口与相关助手能力。已有规划记录和员工画像保持原样。
          </p>
        </SectionCard>
      }
    >
      <SectionCard className="space-y-3 p-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">规划与负荷设置</h2>
          <HelpTip
            label="需求发起权限说明"
            content="此权限适用于规划中的协作需求。选择全体员工后，部门经理和员工都可发起需求。"
          />
        </div>
        <form
          aria-label="负荷与需求规则"
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate(current)
          }}
        >
          <fieldset disabled={save.isPending} className="min-w-0 space-y-3">
            <label className="flex items-center gap-2 text-sm">
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
                <SelectTrigger
                  id="requester-policy"
                  size="sm"
                  className="w-full sm:max-w-xs"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="everyone">全体员工</SelectItem>
                  <SelectItem value="managers">只有部门经理</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <SetupError error={save.error} />
            <Button size="sm" type="submit" disabled={!changes}>
              {save.isPending ? "正在保存…" : "保存设置"}
            </Button>
          </fieldset>
          {save.isSuccess && !changes && (
            <p role="status">负荷与需求规则已保存。</p>
          )}
        </form>
      </SectionCard>
    </RulesLayout>
  )
}
