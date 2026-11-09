import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { isAxiosError } from "axios"
import { useState } from "react"
import {
  CapabilitiesService,
  DraftingSettingsService,
  type DraftingSettingsUpdate,
  OrgsService,
} from "@/client"
import { departmentOptions } from "@/components/Admin/departments"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export function DraftingPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <DraftingSettings key={org.id} orgId={org.id} />
}

function DraftingSettings({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [changes, setChanges] = useState<DraftingSettingsUpdate | null>(null)
  const [message, setMessage] = useState("")
  const settings = useQuery({
    queryKey: ["drafting-settings", orgId],
    queryFn: async () =>
      (
        await DraftingSettingsService.settingsGetDraftingSettings({
          path: { org_id: orgId },
        })
      ).data,
  })
  const capabilities = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
    retry: false,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const save = useMutation({
    mutationFn: (body: DraftingSettingsUpdate) =>
      DraftingSettingsService.settingsPutDraftingSettings({
        path: { org_id: orgId },
        body,
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["drafting-settings", orgId], data)
      client.invalidateQueries({ queryKey: ["auto-draft", orgId] })
      setChanges(null)
      setMessage("已保存，下次生成初始画像时生效。")
    },
    onError: (error) => {
      if (
        isAxiosError(error) &&
        typeof error.response?.data?.detail?.message === "string"
      )
        setMessage(error.response.data.detail.message)
      else handleError.call(setMessage, error)
    },
  })
  if (settings.isPending || departments.isPending || capabilities.isPending)
    return <ProfileLoading />
  const noDictionary =
    isAxiosError(capabilities.error) &&
    capabilities.error.response?.status === 404
  if (
    settings.isError ||
    departments.isError ||
    (capabilities.isError && !noDictionary)
  )
    return (
      <ProfileError
        retry={() => {
          settings.refetch()
          departments.refetch()
          capabilities.refetch()
        }}
      />
    )
  const current: DraftingSettingsUpdate = changes ?? settings.data
  const keys = current.excluded_capability_keys
  const ids = current.disabled_department_ids
  const rows = departments.data?.data ?? []
  function toggle(
    field: "excluded_capability_keys" | "disabled_department_ids",
    value: string,
  ) {
    const values = current[field]
    setChanges({
      ...current,
      [field]: values.includes(value)
        ? values.filter((v) => v !== value)
        : [...values, value],
    })
    setMessage("")
  }
  return (
    <div className="max-w-4xl space-y-6">
      <header className="border-l-4 border-primary pl-4">
        <p className="mb-1 text-xs tracking-widest text-muted-foreground">
          管理 · 自动起草范围
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">自动起草设置</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          这些设置只影响系统自动起草；已有的证据和判断不变。还没过目的初始画像草稿不会删除，但也不会再重新生成。改动在下次生成初始画像时生效。
        </p>
      </header>
      <p className="rounded-xl border bg-muted/20 p-4">
        目前共有 {settings.data?.excluded_employee_count} 名员工不自动起草，其中{" "}
        {settings.data?.opted_out_count} 人是本人选择。
      </p>
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({
            excluded_capability_keys: keys,
            disabled_department_ids: ids,
          })
        }}
      >
        <section
          className="rounded-xl border p-6 space-y-4"
          aria-labelledby="capability-title"
        >
          <h2 id="capability-title" className="text-lg font-semibold">
            不由系统自动推断的能力
          </h2>
          <p className="text-sm text-muted-foreground">
            勾选的能力，系统起草时一律跳过，画像上显示为「未知」，员工可以自己提出。
          </p>
          {noDictionary && <p>企业管理员还没有选择能力词典</p>}
          {[
            ...new Set(
              (capabilities.data?.data ?? []).map(
                (c) => c.category ?? "未分类",
              ),
            ),
          ].map((category) => (
            <fieldset key={category} className="space-y-3">
              <legend className="mb-3 text-sm font-medium text-muted-foreground">
                {category}
              </legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {capabilities.data?.data
                  .filter((c) => (c.category ?? "未分类") === category)
                  .map((c) => (
                    <label
                      key={c.key}
                      htmlFor={`cap-${c.key}`}
                      className="flex items-center gap-3 text-sm"
                    >
                      <Checkbox
                        id={`cap-${c.key}`}
                        checked={keys.includes(c.key)}
                        disabled={save.isPending}
                        onCheckedChange={() =>
                          toggle("excluded_capability_keys", c.key)
                        }
                      />
                      {c.name}
                    </label>
                  ))}
              </div>
            </fieldset>
          ))}
        </section>
        <section
          className="rounded-xl border p-6 space-y-4"
          aria-labelledby="department-title"
        >
          <h2 id="department-title" className="text-lg font-semibold">
            不自动起草的部门
          </h2>
          <p className="text-sm text-muted-foreground">
            勾选后，该部门及其下级部门不再自动起草初始画像；员工仍可自己提出画像更新。
          </p>
          {departmentOptions(rows).map((d) => {
            const ancestors = d.name.split(" / ").length - 1
            let parent = rows.find((r) => r.id === d.id)?.parent_department_id
            const visited = new Set([d.id])
            let included = false
            while (parent && !visited.has(parent)) {
              visited.add(parent)
              if (ids.includes(parent)) included = true
              parent = rows.find((r) => r.id === parent)?.parent_department_id
            }
            return (
              <label
                key={d.id}
                htmlFor={`dept-${d.id}`}
                className="flex items-center gap-3 text-sm"
                style={{ paddingLeft: ancestors * 20 }}
              >
                <Checkbox
                  id={`dept-${d.id}`}
                  checked={included || ids.includes(d.id)}
                  disabled={included || save.isPending}
                  onCheckedChange={() =>
                    toggle("disabled_department_ids", d.id)
                  }
                />
                {rows.find((r) => r.id === d.id)?.name}
                <span className="text-xs text-muted-foreground">
                  {included ? "已包含在上级部门中" : "含下级部门"}
                </span>
              </label>
            )
          })}
        </section>
        {message && <p role={save.isError ? "alert" : "status"}>{message}</p>}
        <Button type="submit" disabled={save.isPending || !changes}>
          {save.isPending ? "正在保存…" : "保存"}
        </Button>
      </form>
    </div>
  )
}
