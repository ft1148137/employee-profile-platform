import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { ApprovalPolicyService, type PolicyInput } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
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
  require_manager: "默认审批规则",
  category_overrides: "按类别例外",
  owner_review_days: "员工确认期限",
}

export function ApprovalPolicy({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [changes, setChanges] = useState<PolicyInput | null>(null)
  const suggestion = usePageDraft(
    orgId,
    "update_approval_policy",
    useCallback(() => setChanges(null), []),
  )
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
  const current = suggestion.values(changes ?? policy.data.current)
  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-muted/30 p-4 text-sm space-y-2">
        <p>
          新规则仅对后续提交生效；免经理审批的能力由员工确认后直接生效（标为「暂定」）。
        </p>
      </div>
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      <form
        aria-label="审批规则"
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault()
          if (suggestion.blocked) return
          save.mutate({
            require_manager: current.require_manager,
            category_overrides: current.category_overrides,
            owner_review_days: current.owner_review_days,
          })
        }}
      >
        <fieldset
          disabled={save.isPending || suggestion.blocked}
          className="space-y-5"
        >
          <DraftField changed={suggestion.changed("require_manager")}>
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
              <span className="font-medium">默认需要经理审批</span>
            </label>
          </DraftField>

          <p className="text-sm text-muted-foreground">
            开启后，员工提交的画像更新需经理批准生效；关闭后员工确认即可生效（标为「暂定」）。
          </p>
          <DraftField changed={suggestion.changed("category_overrides")}>
            <div className="space-y-3">
              <h3 className="font-medium">按能力类别设置</h3>
              <p className="text-sm text-muted-foreground">
                可为特定类别单独设置审批要求，默认跟随上方规则。
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
                      <SelectItem value="inherit">跟随默认</SelectItem>
                      <SelectItem value="required">需要</SelectItem>
                      <SelectItem value="optional">不需要</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
          </DraftField>
          <DraftField changed={suggestion.changed("owner_review_days")}>
            <div className="space-y-2 max-w-sm">
              <Label htmlFor="owner-review-days">员工确认期限（天）</Label>
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
                1–90 天，超期未确认的初始画像可由管理员批量送审。
              </p>
            </div>
          </DraftField>
          <SetupError error={save.error} />
          <Button type="submit" disabled={suggestion.blocked || !changes}>
            {save.isPending ? "正在保存…" : "保存审批规则"}
          </Button>
        </fieldset>
        {save.isSuccess && !changes && <p role="status">审批规则已保存</p>}
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
                经理确认；员工确认期限：{version.owner_review_days} 天
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
      <ChangeHistory targetType="approval_policy" />
    </div>
  )
}
