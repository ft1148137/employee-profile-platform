import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { SetupService } from "@/client"
import { Button } from "@/components/ui/button"
import { useSetup } from "@/hooks/useSetup"
import { ModelStep } from "./ModelStep"
import { OrgProfileStep } from "./OrgProfileStep"
import { SetupError } from "./SetupError"

export function SetupCard({
  orgId,
  step,
}: {
  orgId: string
  step?: "org_profile" | "model"
}) {
  const setup = useSetup()
  const client = useQueryClient()
  const update = useMutation({
    mutationFn: ({ key, skip }: { key: string; skip?: boolean }) =>
      (skip ? SetupService.skipStep : SetupService.completeStep)({
        path: { org_id: orgId, key },
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["setup", orgId], data)
      client.invalidateQueries({
        queryKey: ["admin-assistant-availability", orgId],
      })
      client.invalidateQueries({ queryKey: ["admin-overview", orgId] })
      client.invalidateQueries({
        queryKey: ["conversation-history", orgId, true],
      })
    },
  })
  if (setup.isPending) return <p role="status">正在加载首次设置…</p>
  if (setup.isError)
    return (
      <div>
        <SetupError error={setup.error} />
        <Button onClick={() => setup.refetch()}>重试加载设置</Button>
      </div>
    )
  const state = setup.data.steps.find((item) => item.key === step)
  const title = step === "org_profile" ? "企业名称" : "配置模型"
  const form =
    step === "org_profile" ? (
      <section className="space-y-4" aria-label="企业信息">
        <OrgProfileStep
          orgId={orgId}
          onComplete={() => update.mutate({ key: step })}
        />
        <Button type="submit" form="org-profile" disabled={update.isPending}>
          保存企业名称
        </Button>
      </section>
    ) : (
      <section className="space-y-4" aria-label="配置模型">
        <ModelStep orgId={orgId} />
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={update.isPending}
            onClick={() => update.mutate({ key: "model" })}
          >
            完成模型配置
          </Button>
          {state?.status === "pending" && (
            <Button
              variant="ghost"
              disabled={update.isPending}
              onClick={() => update.mutate({ key: "model", skip: true })}
            >
              暂不配置模型
            </Button>
          )}
        </div>
      </section>
    )
  return (
    <div className="space-y-3 rounded-xl border bg-background p-4">
      {step ? (
        state?.status === "completed" ? (
          <details>
            <summary className="cursor-pointer font-medium">
              {title} · 已完成（展开修改）
            </summary>
            <div className="pt-4">{form}</div>
          </details>
        ) : (
          <>
            <h3 className="font-medium">{title}</h3>
            {form}
            {step === "model" && state?.status === "skipped" && (
              <p role="status">
                模型未配置，配置后回到这条对话继续。
                <Link
                  to="/admin/data-ai"
                  search={{ tab: "model" }}
                  className="underline"
                >
                  数据与 AI → 模型
                </Link>
              </p>
            )}
          </>
        )
      ) : (
        <ol aria-label="设置步骤" className="divide-y">
          {setup.data.steps.map((item) => (
            <li
              key={item.key}
              className="flex items-center justify-between gap-3 py-2"
            >
              <span>
                {item.title}
                {item.required && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    必做
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                {item.status === "completed"
                  ? "已完成"
                  : item.status === "skipped"
                    ? "已跳过"
                    : "待做"}
                {item.skippable && item.status === "pending" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`跳过${item.title}`}
                    disabled={update.isPending}
                    onClick={() => update.mutate({ key: item.key, skip: true })}
                  >
                    跳过
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
      <SetupError error={update.error} />
    </div>
  )
}
