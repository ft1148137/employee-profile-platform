import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { SetupService } from "@/client"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import { useSetup } from "@/hooks/useSetup"
import { SetupError } from "./SetupError"
import { setupSteps } from "./steps"

export function WizardFrame({ orgId }: { orgId: string }) {
  const setup = useSetup()
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const steps = setup.data?.steps ?? []
  const step =
    steps.find((item) => item.key === selected) ??
    steps.find((item) => item.key === setup.data?.next_step_key) ??
    steps[0]
  const index = steps.findIndex((item) => item.key === step?.key)
  const Component = step ? setupSteps[step.key] : undefined
  const update = useMutation({
    mutationFn: ({ key, skip }: { key: string; skip?: boolean }) =>
      (skip ? SetupService.skipStep : SetupService.completeStep)({
        path: { org_id: orgId, key },
      }),
    onSuccess: ({ data }, { key }) => {
      const completedIndex = steps.findIndex((item) => item.key === key)
      client.setQueryData(["setup", orgId], data)
      setSelected(
        data.completed
          ? null
          : (steps[completedIndex + 1]?.key ?? data.next_step_key),
      )
    },
  })
  if (setup.isPending) return <p role="status">正在加载首次设置…</p>
  if (setup.isError)
    return (
      <div className="space-y-4">
        <SetupError error={setup.error} />
        <Button onClick={() => setup.refetch()}>重试</Button>
      </div>
    )
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">首次设置</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          按步骤建立企业的员工画像基础。设置进度会自动保存。
        </p>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="设置步骤">
          <ol className="grid gap-2">
            {steps.map((item, position) => (
              <li key={item.key}>
                <button
                  type="button"
                  disabled={update.isPending}
                  aria-current={
                    step?.key === item.key &&
                    !(setup.data?.completed && !selected)
                      ? "step"
                      : undefined
                  }
                  onClick={() => {
                    setSelected(item.key)
                    update.reset()
                  }}
                  className="w-full rounded-lg border p-3 text-left text-sm aria-[current=step]:border-primary aria-[current=step]:bg-primary/5"
                >
                  <span className="font-medium">
                    {position + 1}. {item.title}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {item.status === "completed"
                      ? "已完成"
                      : item.status === "skipped"
                        ? "已跳过"
                        : "待完成"}
                    {item.required ? " · 必需" : " · 可选"}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <section className="min-w-0 space-y-5 rounded-xl border p-4 sm:p-6">
          {setup.data?.completed && !selected ? (
            <>
              <h2 className="text-xl font-semibold">设置完成</h2>
              <p>企业基础信息已就绪，可以开始使用员工画像平台。</p>
              <ul className="list-disc space-y-2 pl-5 text-sm">
                <li>把账号文件单独交给每位员工，不要发到群里。</li>
                <li>员工第一次登录要修改密码。</li>
                <li>以后有人员变动时，重新导入完整名单。</li>
              </ul>
              <div className="flex flex-wrap gap-2">
                <Button asChild>
                  <Link to="/employees">员工列表</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link to="/admin/roster">导入名单</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link to="/admin/accounts">员工与账号</Link>
                </Button>
              </div>
            </>
          ) : (
            step && (
              <>
                <div>
                  <h2 className="text-xl font-semibold">{step.title}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {step.description}
                  </p>
                </div>
                {Component ? (
                  <Component
                    key={`${orgId}:${step.key}`}
                    orgId={orgId}
                    onComplete={() => update.mutate({ key: step.key })}
                  />
                ) : (
                  <p>此步骤需要更新系统后才能使用</p>
                )}
                <SetupError error={update.error} />
                <div className="flex flex-wrap gap-2 border-t pt-4">
                  <Button
                    variant="outline"
                    disabled={index <= 0 || update.isPending}
                    onClick={() => {
                      setSelected(steps[index - 1].key)
                      update.reset()
                    }}
                  >
                    上一步
                  </Button>
                  <LoadingButton
                    loading={update.isPending}
                    disabled={!Component}
                    type={step.key === "org_profile" ? "submit" : "button"}
                    form={
                      step.key === "org_profile" ? "org-profile" : undefined
                    }
                    onClick={
                      step.key === "org_profile"
                        ? undefined
                        : () => update.mutate({ key: step.key })
                    }
                  >
                    完成此步骤并继续
                  </LoadingButton>
                  {step.skippable && step.status !== "completed" && (
                    <Button
                      variant="ghost"
                      disabled={update.isPending}
                      onClick={() =>
                        update.mutate({ key: step.key, skip: true })
                      }
                    >
                      {step.key === "model" ? "跳过，用关键词规则起草" : "跳过"}
                    </Button>
                  )}
                </div>
              </>
            )
          )}
        </section>
      </div>
    </div>
  )
}
