import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { CapacityService, type DemandInput } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { CapacityError } from "./AssignmentFields"
import {
  capabilityLevels,
  DemandWorkFields,
  demandWork,
  useDemandOptions,
} from "./DemandFields"
import { DemandsReference } from "./DemandsReference"

export function DemandsCreate({
  orgId,
  back,
  created,
}: {
  orgId: string
  back: () => void
  created: (id: string) => void
}) {
  const cache = useQueryClient()
  const [target, setTarget] = useState("")
  const [capability, setCapability] = useState("")
  const [minimum, setMinimum] = useState("")
  const [preview, setPreview] = useState<DemandInput | null>(null)
  const previewHeading = useRef<HTMLHeadingElement>(null)
  const [needsDescription, setNeedsDescription] = useState(false)
  useEffect(() => {
    if (preview) previewHeading.current?.focus()
  }, [preview])
  const options = useDemandOptions(orgId)
  const reference = useQuery({
    queryKey: ["capacity", orgId, "reference", target, capability, minimum],
    queryFn: async () =>
      (
        await CapacityService.capacityReference({
          path: { org_id: orgId },
          query: {
            target_department_id: target,
            capability_key: capability,
            minimum_level: minimum || undefined,
          },
        })
      ).data,
    enabled: !!target && !!capability,
  })
  const submit = useMutation({
    mutationKey: ["capacity", orgId, "create", "outgoing"],
    mutationFn: (body: DemandInput) =>
      CapacityService.submitDemand({ path: { org_id: orgId }, body }),
    onSuccess: async ({ data }) => {
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["capacity", orgId] }),
        cache.invalidateQueries({ queryKey: ["inbox", orgId] }),
      ])
      created(data.id)
    },
  })
  if (options.isPending) return <ProfileLoading />
  if (options.isError) return <ProfileError retry={() => options.refetch()} />
  const { departments, capabilities, requesting } = options.data
  const selectedCapability = capabilities.find((c) => c.key === capability)
  const levels = capabilityLevels(selectedCapability?.level_rubric)
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (preview) {
          if (!submit.isPending) submit.mutate(preview)
          return
        }
        const data = new FormData(event.currentTarget)
        const work = demandWork(data)
        setNeedsDescription(!work.description)
        if (!work.description) return
        setPreview({
          requesting_department_id: String(data.get("requesting")),
          target_department_id: target,
          capability_key: capability,
          minimum_level: minimum || null,
          ...work,
        })
      }}
    >
      <div hidden={!!preview} className="space-y-4">
        <fieldset disabled={!!preview} className="grid gap-4 sm:grid-cols-2">
          {[
            {
              name: "requesting",
              label: "需求方部门",
              items: requesting.map((d) => ({ key: d.id, label: d.name })),
            },
            {
              name: "target",
              label: "目标部门",
              value: target,
              change: setTarget,
              items: departments.map((d) => ({ key: d.id, label: d.name })),
            },
            {
              name: "capability",
              label: "能力",
              value: capability,
              change: (value: string) => {
                setCapability(value)
                setMinimum("")
              },
              items: capabilities.map((c) => ({ key: c.key, label: c.name })),
            },
            {
              name: "minimum",
              label: "最低等级",
              value: minimum,
              change: setMinimum,
              items: levels.map((l) => ({
                key: String(l.key),
                label: String(l.label || l.key),
              })),
            },
          ].map(({ name, label, items, value, change }) => (
            <div key={name} className="space-y-2 text-sm font-medium">
              <label htmlFor={`demand-${name}`}>{label}</label>
              <select
                id={`demand-${name}`}
                name={name}
                required={name !== "minimum"}
                value={value}
                onChange={
                  change ? (event) => change(event.target.value) : undefined
                }
                className="h-10 w-full rounded-md border bg-background px-3"
              >
                <option value="">
                  {name === "minimum" ? "不限" : "请选择"}
                </option>
                {items.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
          <DemandWorkFields />
          {needsDescription && (
            <p role="alert" className="text-sm text-destructive sm:col-span-2">
              请填写需求说明。
            </p>
          )}
          <Button
            type="submit"
            disabled={!requesting.length || !target || !capability}
          >
            预览需求
          </Button>
        </fieldset>
        <Button
          type="button"
          variant="ghost"
          onClick={back}
          disabled={submit.isPending}
        >
          取消
        </Button>
      </div>
      {!preview && target && capability && (
        <section
          aria-label="即时参考"
          className="space-y-3 rounded-xl border bg-muted/30 p-5"
          aria-live="polite"
        >
          <h3 className="font-semibold">即时参考</h3>
          {reference.isPending ? (
            <ProfileLoading />
          ) : reference.isError ? (
            <ProfileError retry={() => reference.refetch()} />
          ) : (
            <DemandsReference reference={reference.data} />
          )}
          <p className="text-sm text-muted-foreground">
            仅供参考，能否承接以目标部门经理正式答复为准。
          </p>
        </section>
      )}
      {preview && (
        <section
          aria-label="需求预览"
          className="space-y-3 rounded-xl border p-5"
        >
          <h3 ref={previewHeading} tabIndex={-1} className="font-semibold">
            需求预览
          </h3>
          <p>
            {
              departments.find((d) => d.id === preview.requesting_department_id)
                ?.name
            }{" "}
            →{" "}
            {
              departments.find((d) => d.id === preview.target_department_id)
                ?.name
            }
          </p>
          <p>
            {selectedCapability?.name} · 最低等级：
            {levels
              .find((l) => l.key === preview.minimum_level)
              ?.label?.toString() || "不限"}
          </p>
          <p>
            {preview.headcount} 人 · 每人 {preview.allocation_percent}% ·{" "}
            {preview.window_start} 至 {preview.window_end}
          </p>
          <p className="whitespace-pre-wrap break-words">
            {preview.description}
          </p>
          <div className="flex gap-3">
            <Button type="submit" disabled={submit.isPending}>
              {submit.isPending ? "正在提交…" : "确认提交需求"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={submit.isPending}
              onClick={() => {
                setPreview(null)
                submit.reset()
              }}
            >
              返回修改
            </Button>
          </div>
          <CapacityError error={submit.error} />
        </section>
      )}
    </form>
  )
}
