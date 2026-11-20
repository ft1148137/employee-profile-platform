import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useId } from "react"
import { DraftingSettingsService } from "@/client"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"

export function AutoDraftSetting({ orgId }: { orgId: string }) {
  const labelId = useId()
  const client = useQueryClient()
  const queryKey = ["auto-draft", orgId]
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetAutoDraft({
          path: { org_id: orgId },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (enabled: boolean) =>
      DraftingSettingsService.settingsPutAutoDraft({
        path: { org_id: orgId },
        body: { enabled },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(queryKey, data)
      client.invalidateQueries({ queryKey: ["drafting-settings", orgId] })
    },
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  return (
    <SectionCard aria-label="自动起草偏好" className="space-y-2 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span id={labelId} className="text-sm font-medium">
            根据工作记录自动起草画像
          </span>
          <HelpTip
            label="关于自动起草偏好"
            content="关闭后不再为你自动生成草稿，已有画像不受影响，可随时重新开启。"
          />
        </div>
        <Button
          type="button"
          size="sm"
          role="switch"
          aria-labelledby={labelId}
          aria-checked={query.data.enabled}
          disabled={save.isPending}
          variant={query.data.enabled ? "default" : "outline"}
          onClick={() => save.mutate(!query.data.enabled)}
        >
          {query.data.enabled ? "已开启" : "已关闭"}
        </Button>
      </div>
      {query.data.org_disabled && (
        <p className="text-sm">企业暂未开启自动起草功能。</p>
      )}
      {save.isError && <p role="alert">保存失败，请重试。</p>}
    </SectionCard>
  )
}
