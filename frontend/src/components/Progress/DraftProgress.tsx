import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { InitialProfilesService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

const labels: Record<string, string> = {
  not_started: "未开始",
  pending: "排队中",
  running: "起草中",
  generated: "已生成",
  failed: "失败",
  excluded: "不自动起草",
}

export function DraftProgress({
  orgId,
  wizard = false,
}: {
  orgId: string
  wizard?: boolean
}) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const progress = useQuery({
    queryKey: ["draft-progress", orgId],
    queryFn: async () =>
      (await InitialProfilesService.profilesProgress({ path })).data,
    refetchInterval: 5000,
  })
  // Saved pending progress can outlive its job; the API rejects concurrent drafts.
  const draft = useMutation({
    mutationFn: (scope: "all" | "failed") =>
      InitialProfilesService.profilesDraft({ path, body: { scope } }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["draft-progress", orgId] })
      client.invalidateQueries({ queryKey: ["setup", orgId] })
    },
  })
  const data = progress.data
  const counts = data?.counts ?? {}
  const busy = !!(counts.pending || counts.running)
  const generated =
    (data?.requests.pending_owner ?? 0) + (data?.requests.handed_over ?? 0)
  return (
    <section className="space-y-5" aria-label="初始画像起草进度">
      <h2 className="text-xl font-semibold">初始画像</h2>
      <SetupError error={progress.error ?? draft.error} />
      {progress.isPending && <p role="status">正在读取起草进度…</p>}
      {progress.isError && (
        <Button onClick={() => progress.refetch()}>重新读取</Button>
      )}
      {data && (
        <>
          {data.mode === "none" ? (
            <div role="alert" className="rounded-lg border p-4">
              <p>
                还没有配置模型，请先在设置页配置模型，或者让员工用 JSON 导入画像
              </p>
              <Link className="underline" to="/admin/model">
                去配置模型
              </Link>
            </div>
          ) : (
            <p>当前方式：模型</p>
          )}
          {data.paused_until && (
            <div
              role="alert"
              className="rounded-lg border border-amber-500 bg-amber-50 p-4 text-amber-950"
            >
              <p>{data.pause_reason ?? "起草暂时暂停"}</p>
              <p>
                预计 {new Date(data.paused_until).toLocaleString("zh-CN")}{" "}
                自动继续
              </p>
            </div>
          )}
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.entries(counts).map(([key, count]) => (
              <div key={key} className="rounded-lg border p-4">
                <dt className="text-sm text-muted-foreground">
                  {labels[key] ?? "其他"}
                </dt>
                <dd className="mt-2 text-3xl tabular-nums">{count}</dd>
              </div>
            ))}
          </dl>
          {generated > 0 && !busy && !counts.failed && !counts.not_started && (
            <p role="status">
              已为 {generated}{" "}
              名员工生成初始画像，员工登录后能在自己的画像页看到
            </p>
          )}
          {!!counts.excluded && (
            <p>
              {counts.excluded} 名员工不自动起草（部门关闭或本人选择），可以在
              <Link className="underline" to="/admin/drafting">
                自动起草设置
              </Link>
              里修改
            </p>
          )}
          {data.failed.length > 0 && (
            <ul className="space-y-2">
              {data.failed.map((person) => (
                <li
                  key={person.employee_id}
                  className="rounded-lg border border-destructive/30 p-3"
                >
                  {person.display_name}：{person.error}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <div className="flex flex-wrap gap-3">
        <Button
          disabled={draft.isPending || !data || data.mode === "none"}
          onClick={() => {
            if (
              wizard ||
              window.confirm("只覆盖员工还没过目的草稿，确定重新起草全部？")
            )
              draft.mutate("all")
          }}
        >
          {wizard ? "生成初始画像" : "重新起草全部"}
        </Button>
        <Button
          variant="outline"
          disabled={draft.isPending || !counts.failed || data?.mode === "none"}
          onClick={() => draft.mutate("failed")}
        >
          重试失败的
        </Button>
      </div>
    </section>
  )
}
