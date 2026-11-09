import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { ApprovalPolicyService, type PolicyInput } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const fieldLabels: Record<string, string> = {
  require_manager: "全企业审批要求",
  category_overrides: "能力类别覆盖",
  owner_review_days: "员工过目期限",
}

export function ApprovalPolicy({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [changes, setChanges] = useState<PolicyInput | null>(null)
  const policy = useQuery({
    queryKey: ["approval-policy", orgId],
    queryFn: async () =>
      (
        await ApprovalPolicyService.policyGetApprovalPolicy({
          path: { org_id: orgId },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (body: PolicyInput) =>
      ApprovalPolicyService.policyPutApprovalPolicy({
        path: { org_id: orgId },
        body,
      }),
    onSuccess: ({ data }) => {
      client.setQueryData(["approval-policy", orgId], data)
      client.invalidateQueries({ queryKey: ["handover", orgId] })
      setChanges(null)
    },
  })
  if (policy.isPending) return <ProfileLoading />
  if (policy.isError) return <ProfileError retry={() => policy.refetch()} />
  const current = changes ?? policy.data.current
  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-muted/30 p-4 text-sm space-y-2">
        <p>不需要经理确认的能力，员工提交后直接发布为暂定。</p>
        <p className="text-muted-foreground">
          规则只影响之后的提交；管理员批量送审仍由经理审批。默认需要经理确认，员工过目期限为
          14 天。
        </p>
      </div>
      <form
        aria-label="审批规则"
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate({
            require_manager: current.require_manager,
            category_overrides: current.category_overrides,
            owner_review_days: current.owner_review_days,
          })
        }}
      >
        <fieldset disabled={save.isPending} className="space-y-5">
          <label className="flex items-center gap-3 rounded-lg border p-4">
            <input
              type="checkbox"
              checked={current.require_manager}
              onChange={(event) =>
                setChanges({
                  ...current,
                  require_manager: event.target.checked,
                })
              }
            />
            <span className="font-medium">全企业需要经理确认</span>
          </label>
          <p className="text-sm text-muted-foreground">
            打开后员工提交的判断须经经理批准才发布；关闭后由员工本人确认并标为暂定。
          </p>
          <div className="space-y-3">
            <h3 className="font-medium">按能力类别设置</h3>
            <p className="text-sm text-muted-foreground">
              单独设置的类别优先于全企业要求；选择「跟随全企业」即可使用上面的规则。
            </p>
            {policy.data.categories.length === 0 && (
              <p className="text-sm text-muted-foreground">
                当前词典没有能力类别，使用全企业设置。
              </p>
            )}
            {policy.data.categories.map((category, index) => (
              <div
                key={category}
                className="grid items-center gap-2 sm:grid-cols-2"
              >
                <Label htmlFor={`policy-category-${index}`}>{category}</Label>
                <Select
                  value={
                    current.category_overrides?.[category] === undefined
                      ? "inherit"
                      : current.category_overrides[category]
                        ? "required"
                        : "optional"
                  }
                  onValueChange={(value) => {
                    const overrides = { ...current.category_overrides }
                    if (value === "inherit") delete overrides[category]
                    else overrides[category] = value === "required"
                    setChanges({ ...current, category_overrides: overrides })
                  }}
                >
                  <SelectTrigger id={`policy-category-${index}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inherit">跟随全企业</SelectItem>
                    <SelectItem value="required">需要</SelectItem>
                    <SelectItem value="optional">不需要</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
          <div className="space-y-2 max-w-sm">
            <Label htmlFor="owner-review-days">员工过目期限（天）</Label>
            <Input
              id="owner-review-days"
              type="number"
              min={1}
              max={90}
              step={1}
              required
              value={current.owner_review_days}
              onChange={(event) =>
                setChanges({
                  ...current,
                  owner_review_days: Number(event.target.value),
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              1–90 天。到期后管理员可以批量送审。
            </p>
          </div>
          <SetupError error={save.error} />
          <Button type="submit" disabled={!changes}>
            {save.isPending ? "正在保存…" : "保存审批规则"}
          </Button>
        </fieldset>
        {save.isSuccess && !changes && (
          <p role="status">审批规则已保存为新版本。</p>
        )}
      </form>
      <section aria-label="审批规则历史" className="border-t pt-5 space-y-3">
        <h3 className="font-medium">历史版本</h3>
        {policy.data.history.length === 0 && (
          <p className="text-sm text-muted-foreground">
            尚未保存，当前使用默认规则。
          </p>
        )}
        <ol className="space-y-3">
          {policy.data.history.map((version) => (
            <li
              key={version.version}
              className="rounded-lg border p-4 text-sm space-y-2"
            >
              <p className="font-medium">
                第 {version.version} 版 · {version.created_by_name}
              </p>
              <p className="text-muted-foreground">
                {version.created_at &&
                  new Date(version.created_at).toLocaleString("zh-CN")}{" "}
                ·{" "}
                {(version.changed_fields ?? [])
                  .map((field) => fieldLabels[field])
                  .join("、") || "保存当前规则"}
              </p>
              <p>
                全企业：{version.require_manager ? "需要" : "不需要"}
                经理确认；员工过目期限：{version.owner_review_days} 天
              </p>
              {Object.entries(version.category_overrides ?? {}).map(
                ([category, required]) => (
                  <p key={category}>
                    {category}：{required ? "需要" : "不需要"}经理确认
                  </p>
                ),
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
