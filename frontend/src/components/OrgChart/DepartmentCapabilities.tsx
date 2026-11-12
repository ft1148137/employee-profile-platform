import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapabilitiesService, OrgsService } from "@/client"
import { CapabilityPicker } from "@/components/Profile/CapabilityPicker"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function DepartmentCapabilities({
  orgId,
  departmentId,
  canEdit,
}: {
  orgId: string
  departmentId: string
  canEdit: boolean
}) {
  const client = useQueryClient()
  const [draft, setDraft] = useState<string[] | null>(null)
  const [picking, setPicking] = useState(false)
  const [saved, setSaved] = useState(false)
  const queryKey = ["department-capabilities", orgId, departmentId]
  const path = { org_id: orgId, id: departmentId }
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      (await OrgsService.readDepartmentCapabilities({ path })).data,
  })
  const dictionary = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
    enabled: canEdit && picking,
  })
  const save = useMutation({
    mutationFn: async () =>
      (
        await OrgsService.updateDepartmentCapabilities({
          path,
          body: { capability_keys: draft ?? query.data!.capability_keys },
        })
      ).data,
    onSuccess: (data) => {
      client.setQueryData(queryKey, data)
      client.invalidateQueries({ queryKey: ["department-capabilities", orgId] })
      client.invalidateQueries({ queryKey: ["profile", orgId] })
      setDraft(null)
      setSaved(true)
    },
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  const own = draft ?? query.data.capability_keys
  const inherited = query.data.inherited_capability_keys
  const name = (key: string) =>
    query.data.capabilities.find((item) => item.key === key)?.name ??
    dictionary.data?.data.find((item) => item.key === key)?.name ??
    key
  return (
    <section
      aria-label="对应能力"
      className="space-y-4 rounded-xl border bg-card p-5"
    >
      <div>
        <h2 className="font-semibold">对应能力</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          子部门自动继承上级能力，员工画像默认显示本部门对应的能力。
        </p>
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        <div className="space-y-2">
          <h3 className="text-sm font-medium">继承的能力（只读）</h3>
          {inherited.map((key) => (
            <p key={key} className="text-sm">
              {name(key)} · 继承自上级部门
            </p>
          ))}
          {!inherited.length && (
            <p className="text-sm text-muted-foreground">暂无继承能力</p>
          )}
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-medium">本部门设置</h3>
          {own.map((key) => (
            <div
              key={key}
              className="flex items-center justify-between gap-3 text-sm"
            >
              <span>{name(key)}</span>
              {canEdit && (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`移除${name(key)}`}
                  disabled={save.isPending}
                  onClick={() => {
                    setDraft(own.filter((item) => item !== key))
                    setSaved(false)
                  }}
                >
                  移除
                </Button>
              )}
            </div>
          ))}
          {!own.length && (
            <p className="text-sm text-muted-foreground">暂无本部门设置</p>
          )}
        </div>
      </div>
      <SetupError error={save.error} />
      {saved && (
        <p role="status" className="text-sm">
          对应能力已保存
        </p>
      )}
      {canEdit && (
        <div className="flex gap-3">
          <Button
            variant="outline"
            disabled={save.isPending}
            onClick={() => setPicking(true)}
          >
            添加能力
          </Button>
          <Button
            disabled={draft === null || save.isPending}
            onClick={() => save.mutate()}
          >
            保存对应能力
          </Button>
        </div>
      )}
      {picking &&
        (dictionary.isPending ? (
          <ProfileLoading />
        ) : dictionary.isError ? (
          <ProfileError retry={() => dictionary.refetch()} />
        ) : (
          <CapabilityPicker
            capabilities={dictionary.data.data.filter(
              (item) => !own.includes(item.key),
            )}
            onClose={() => setPicking(false)}
            onSelect={(key) => {
              setDraft([...own, key])
              setSaved(false)
              setPicking(false)
            }}
          />
        ))}
    </section>
  )
}
