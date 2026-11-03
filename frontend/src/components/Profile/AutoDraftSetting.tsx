import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DraftingSettingsService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"

export function AutoDraftSetting({ orgId }: { orgId: string }) {
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
    <section
      aria-label="自动起草偏好"
      className="rounded-xl border bg-muted/20 p-5 space-y-3"
    >
      <div className="flex items-center justify-between gap-4">
        <span id="auto-draft-label" className="font-medium">
          让系统根据工作记录自动起草我的画像
        </span>
        <Button
          role="switch"
          aria-labelledby="auto-draft-label"
          aria-checked={query.data.enabled}
          disabled={save.isPending}
          variant={query.data.enabled ? "default" : "outline"}
          onClick={() => save.mutate(!query.data.enabled)}
        >
          {query.data.enabled ? "已开启" : "已关闭"}
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        关闭后，系统不再为你自动起草画像建议。已有的证据和判断不受影响，你可以随时打开。
      </p>
      {query.data.department_disabled && (
        <p className="text-sm">你所在的部门目前关闭了自动起草。</p>
      )}
      {save.isError && <p role="alert">保存失败，请重试。</p>}
    </section>
  )
}
