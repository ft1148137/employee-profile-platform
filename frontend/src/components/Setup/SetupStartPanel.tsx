import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { type ReactNode, useState } from "react"
import { SetupService } from "@/client"
import { Button } from "@/components/ui/button"
import { useSetup } from "@/hooks/useSetup"
import { ModelStep } from "./ModelStep"
import { OrgProfileStep } from "./OrgProfileStep"
import { SetupError } from "./SetupError"

export function SetupStartPanel({
  orgId,
  available,
  onStart,
  children,
}: {
  orgId: string
  available: boolean
  onStart: () => void
  children: ReactNode
}) {
  const setup = useSetup()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  const update = useMutation({
    mutationFn: ({ key, skip }: { key: string; skip?: boolean }) =>
      (skip ? SetupService.skipStep : SetupService.completeStep)({
        path: { org_id: orgId, key },
      }),
    onSuccess: ({ data }, { key, skip }) => {
      client.setQueryData(["setup", orgId], data)
      client.invalidateQueries({
        queryKey: ["admin-assistant-availability", orgId],
      })
      client.invalidateQueries({ queryKey: ["admin-overview", orgId] })
      if (key === "model") {
        if (skip) navigate({ to: "/admin" })
        else setReady(true)
      }
    },
  })
  if (setup.isPending) return <p role="status">正在加载首次设置…</p>
  if (setup.isError)
    return (
      <div className="space-y-3">
        <SetupError error={setup.error} />
        <Button onClick={() => setup.refetch()}>重试加载设置</Button>
      </div>
    )
  if (setup.data.completed && !ready) return children
  return (
    <section className="space-y-6 py-4" aria-label="开始使用">
      <div className="space-y-2">
        <p className="text-xs tracking-widest text-muted-foreground">
          企业管理助手
        </p>
        <h2 className="text-2xl font-semibold">开始使用</h2>
        <p className="text-muted-foreground">
          先核对企业信息并启用模型，接下来由助手带你逐步完成设置。
        </p>
      </div>
      <ol aria-label="设置步骤" className="grid gap-2 sm:grid-cols-2">
        {setup.data.steps.map((step) => (
          <li
            key={step.key}
            className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
          >
            <span>{step.title}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {step.status === "completed"
                ? "已完成"
                : step.status === "skipped"
                  ? "已跳过"
                  : "待做"}
            </span>
          </li>
        ))}
      </ol>
      {ready ? (
        <div className="space-y-4 rounded-xl border bg-muted/30 p-5">
          <p>模型已就绪，接下来我带你完成剩下的设置</p>
          <Button disabled={!available} onClick={onStart}>
            开始
          </Button>
        </div>
      ) : (
        <div className="space-y-6 rounded-xl border p-5">
          <section className="space-y-4" aria-label="企业信息">
            <h3 className="font-semibold">企业名称</h3>
            <OrgProfileStep
              orgId={orgId}
              onComplete={() => update.mutate({ key: "org_profile" })}
            />
            <Button
              type="submit"
              form="org-profile"
              disabled={update.isPending}
            >
              保存企业名称
            </Button>
          </section>
          <section className="space-y-4 border-t pt-5" aria-label="配置模型">
            <h3 className="font-semibold">配置模型</h3>
            <ModelStep orgId={orgId} />
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={update.isPending}
                onClick={() => update.mutate({ key: "model" })}
              >
                完成模型配置
              </Button>
              <Button
                variant="ghost"
                disabled={update.isPending}
                onClick={() => update.mutate({ key: "model", skip: true })}
              >
                暂不配置模型
              </Button>
            </div>
          </section>
        </div>
      )}
      <SetupError error={update.error} />
      <p className="text-xs text-muted-foreground">
        以后也可以从
        <Link
          to="/admin/data-ai"
          search={{ tab: "model" }}
          className="underline"
        >
          数据与 AI → 模型
        </Link>
        修改配置。
      </p>
    </section>
  )
}
