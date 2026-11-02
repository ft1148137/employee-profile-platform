import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useEffect, useRef, useState } from "react"
import { CapabilitiesService, CapabilityTemplatesService } from "@/client"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import { SetupError } from "../SetupError"
import type { StepProps } from "."

export function CapabilitiesStep({ orgId }: StepProps) {
  const client = useQueryClient()
  const previewRef = useRef<HTMLElement>(null)
  const [previewId, setPreviewId] = useState("")
  const catalog = useQuery({
    queryKey: ["capability-templates"],
    queryFn: async () =>
      (await CapabilityTemplatesService.templatesReadTemplates()).data,
  })
  const current = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () => {
      try {
        return (
          await CapabilitiesService.readCapabilities({
            path: { org_id: orgId },
          })
        ).data
      } catch (error) {
        if (error instanceof AxiosError && error.response?.status === 404)
          return null
        throw error
      }
    },
  })
  const preview = useQuery({
    queryKey: ["capability-template", previewId],
    queryFn: async () =>
      (
        await CapabilityTemplatesService.templatesPreviewTemplate({
          path: { template_id: previewId },
        })
      ).data,
    enabled: !!previewId,
  })
  useEffect(() => {
    if (preview.data) previewRef.current?.scrollIntoView({ block: "start" })
  }, [preview.data])
  const activate = useMutation({
    mutationFn: () =>
      CapabilityTemplatesService.templatesActivateCapabilitySchema({
        path: { org_id: orgId },
        body: { template_id: previewId },
      }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["capabilities", orgId] }),
        client.invalidateQueries({ queryKey: ["setup", orgId] }),
      ])
    },
  })
  const activeName = catalog.data?.find(
    (item) =>
      item.id ===
      current.data?.schema_version.template_ref
        ?.split("@")[0]
        .replace(/\.zh-CN$/, ""),
  )?.name
  return (
    <div className="space-y-5">
      {current.data ? (
        <div className="rounded-lg border bg-muted/40 p-4">
          <p className="font-medium">当前词典：{activeName ?? "已启用词典"}</p>
          <p className="text-sm text-muted-foreground">
            共 {current.data.data.length} 项能力 · 第{" "}
            {current.data.schema_version.version} 版
          </p>
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer">查看当前能力</summary>
            <ul className="mt-2 space-y-1">
              {current.data.data.map((item) => (
                <li key={item.id}>
                  {item.name} · {item.category}
                  <p className="text-muted-foreground">{item.description}</p>
                </li>
              ))}
            </ul>
          </details>
        </div>
      ) : (
        current.isSuccess && (
          <p className="text-sm text-muted-foreground">
            尚未选择能力词典，请先预览适合你的行业模板。
          </p>
        )
      )}
      {(catalog.isPending || current.isPending) && (
        <p role="status">正在加载能力词典…</p>
      )}
      <SetupError error={catalog.error ?? current.error} />
      {(catalog.isError || current.isError) && (
        <Button
          variant="outline"
          onClick={() => {
            catalog.refetch()
            current.refetch()
          }}
        >
          重试
        </Button>
      )}
      <div className="grid gap-3 xl:grid-cols-2">
        {catalog.data?.map((item) => (
          <button
            type="button"
            key={item.id}
            disabled={activate.isPending}
            aria-pressed={previewId === item.id}
            onClick={() => {
              setPreviewId(item.id)
              activate.reset()
            }}
            className={`rounded-xl border p-4 text-left transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring ${previewId === item.id ? "border-primary bg-primary/5" : ""}`}
          >
            <span className="flex flex-wrap items-center gap-2 font-semibold">
              {item.name}
              {item.recommended && (
                <span className="rounded bg-primary px-2 py-0.5 text-xs text-primary-foreground">
                  推荐
                </span>
              )}
            </span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {item.industry} · {item.capability_count} 项能力
            </span>
            <span className="mt-2 block text-sm">{item.description}</span>
            <span className="mt-3 block text-sm text-primary">
              预览能力列表
            </span>
          </button>
        ))}
      </div>
      {previewId && (
        <section
          ref={previewRef}
          aria-label="词典预览"
          className="space-y-4 rounded-xl border p-4"
        >
          {preview.isPending && <p role="status">正在加载预览…</p>}
          <SetupError error={preview.error} />
          {preview.isError && (
            <Button onClick={() => preview.refetch()}>重新加载预览</Button>
          )}
          {preview.data && (
            <>
              <h3 className="font-semibold">{preview.data.name} · 能力预览</h3>
              <div className="max-h-96 space-y-4 overflow-y-auto">
                {preview.data.capabilities.map((item) => (
                  <article
                    key={item.key}
                    className="border-b pb-4 last:border-0"
                  >
                    <h4 className="font-medium">
                      {item.name}{" "}
                      <span className="text-xs text-muted-foreground">
                        {item.category}
                      </span>
                    </h4>
                    <p className="my-2 text-sm">{item.description}</p>
                    <ol className="space-y-2 text-sm text-muted-foreground">
                      {item.levels.map((level, index) => (
                        <li key={level.key}>
                          第 {index + 1} 级 · {level.label}：{level.description}
                        </li>
                      ))}
                    </ol>
                  </article>
                ))}
              </div>
              <LoadingButton
                loading={activate.isPending}
                onClick={() => activate.mutate()}
              >
                启用此词典
              </LoadingButton>
            </>
          )}
          <SetupError error={activate.error} />
          {activate.isSuccess && (
            <p role="status" className="text-sm">
              词典已启用，可以完成此步骤。
            </p>
          )}
        </section>
      )}
    </div>
  )
}
