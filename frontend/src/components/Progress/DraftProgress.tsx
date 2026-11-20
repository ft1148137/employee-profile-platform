import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  ApprovalPolicyService,
  DraftingSettingsService,
  InitialProfilesService,
} from "@/client"
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
  const settings = useQuery({
    queryKey: ["drafting-settings", orgId],
    queryFn: async () =>
      (await DraftingSettingsService.settingsGetDraftingSettings({ path }))
        .data,
  })
  const policy = useQuery({
    queryKey: ["approval-policy", orgId],
    queryFn: async () =>
      (await ApprovalPolicyService.policyGetApprovalPolicy({ path })).data,
    enabled: !wizard,
  })
  const progress = useQuery({
    queryKey: ["draft-progress", orgId],
    queryFn: async () =>
      (await InitialProfilesService.profilesProgress({ path })).data,
    refetchInterval: 5000,
  })
  const handover = useQuery({
    queryKey: ["handover", orgId],
    queryFn: async () =>
      (await InitialProfilesService.profilesPreviewHandover({ path })).data,
    enabled: !wizard,
    refetchInterval: 5000,
  })
  const send = useMutation({
    mutationFn: () => InitialProfilesService.profilesConfirmHandover({ path }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["draft-progress", orgId] })
      client.invalidateQueries({ queryKey: ["handover", orgId] })
    },
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
      {!wizard && (
        <div className="space-y-3 rounded-lg border p-4">
          <p>
            满 {policy.data?.current.owner_review_days ?? "—"}{" "}
            天未处理、可批量送审：{handover.data?.count ?? "—"} 人
          </p>
          <p className="text-sm text-muted-foreground">
            送审后标为「员工未确认」，通知本人，批准前本人可以撤回。
          </p>
          <SetupError error={handover.error ?? send.error} />
          <Button
            variant="outline"
            disabled={send.isPending || !handover.data?.count}
            onClick={async () => {
              const latest = await handover.refetch()
              if (
                latest.isSuccess &&
                latest.data.count > 0 &&
                window.confirm(
                  `确认将 ${latest.data.count} 名员工的初始画像批量送审？这些员工尚未确认。`,
                )
              )
                send.mutate()
            }}
          >
            批量送审
          </Button>
          {send.isSuccess && (
            <p role="status">已送审 {send.data.data.count} 人</p>
          )}
        </div>
      )}
      {progress.isPending && <p role="status">正在读取起草进度…</p>}
      {progress.isError && (
        <Button onClick={() => progress.refetch()}>重新读取</Button>
      )}
      {data && (
        <>
          {!settings.data?.enabled && (
            <p>自动起草未开启，请先前往「设置 → 模型」开启。</p>
          )}
          {data.mode === "none" && (
            <div role="alert" className="rounded-lg border p-4">
              <p>
                尚未配置 AI 模型，请先前往「设置 →
                模型」配置，或由员工手动/导入维护画像。
              </p>
              <Link className="underline" to="/admin/model">
                去配置模型
              </Link>
            </div>
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
              {counts.excluded}{" "}
              名员工本人选择不参与自动起草，可由员工在个人设置里修改。
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
          disabled={
            draft.isPending ||
            !data ||
            data.mode === "none" ||
            !settings.data?.enabled
          }
          onClick={() => {
            if (
              wizard ||
              window.confirm(
                "将仅覆盖员工尚未确认的草稿，已发布内容不受影响。确认重新起草吗？",
              )
            )
              draft.mutate("all")
          }}
        >
          {wizard ? "生成初始画像" : "重新起草全部"}
        </Button>
        <Button
          variant="outline"
          disabled={
            draft.isPending ||
            !counts.failed ||
            data?.mode === "none" ||
            !settings.data?.enabled
          }
          onClick={() => draft.mutate("failed")}
        >
          重试失败项
        </Button>
      </div>
    </section>
  )
}
