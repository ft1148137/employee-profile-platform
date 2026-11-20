import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { ApprovalPolicyService, type PolicyInput } from "@/client"
import { ChangeHistory } from "@/components/Admin/ChangeHistory"
import { RulesLayout } from "@/components/Admin/RulesLayout"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
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
  const published = policy.data.current
  const current = suggestion.values(changes ?? published)
  const exceptionCount = policy.data.categories.filter(
    (category) => published.category_overrides?.[category] !== undefined,
  ).length
  return (
    <RulesLayout
      summary={[
        [
          "默认策略",
          published.require_manager ? "经理审批" : "员工确认后发布为暂定",
        ],
        ["例外类别", `${exceptionCount} 条规则`],
        ["员工确认期限", `${published.owner_review_days} 天`],
        [
          "当前版本",
          published.version ? `第 ${published.version} 版` : "默认规则",
        ],
      ]}
      history={
        <>
          <ChangeHistory targetType="approval_policy" variant="compact" />
          {policy.data.history.length > 0 && (
            <SectionCard aria-label="审批规则历史" className="p-3">
              <details>
                <summary className="cursor-pointer text-sm font-semibold">
                  历史版本
                </summary>
                <ol className="mt-2 divide-y text-xs wrap-anywhere">
                  {policy.data.history.map((version) => (
                    <li key={version.version} className="space-y-1 py-2">
                      <p className="font-medium">
                        第 {version.version} 版 · {version.created_by_name}
                      </p>
                      <p className="text-muted-foreground">
                        {version.created_at &&
                          new Date(version.created_at).toLocaleString(
                            "zh-CN",
                          )}{" "}
                        ·{" "}
                        {(version.changed_fields ?? [])
                          .map((field) => fieldLabels[field])
                          .join("、") || "保存当前规则"}
                      </p>
                      <p>
                        全企业：{version.require_manager ? "需要" : "不需要"}
                        经理审批；员工确认期限：{version.owner_review_days} 天
                      </p>
                      {Object.entries(version.category_overrides ?? {}).map(
                        ([category, required]) => (
                          <p key={category}>
                            {category}：{required ? "需要" : "不需要"}经理审批
                          </p>
                        ),
                      )}
                    </li>
                  ))}
                </ol>
              </details>
            </SectionCard>
          )}
        </>
      }
    >
      <PageDraft
        orgId={orgId}
        draft={suggestion.draft}
        error={suggestion.error}
      />
      <form
        aria-label="审批规则"
        className="space-y-3"
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
          className="min-w-0 space-y-3"
        >
          <SectionCard className="space-y-3 p-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">默认画像审批策略</h2>
              <HelpTip
                label="默认审批策略说明"
                content="新规则适用于后续提交。免经理审批的能力由员工确认后直接发布为暂定。"
              />
            </div>
            <DraftField changed={suggestion.changed("require_manager")}>
              <label className="flex items-center gap-2 text-sm font-medium">
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
                默认需要经理审批
              </label>
            </DraftField>
            <DraftField changed={suggestion.changed("owner_review_days")}>
              <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor="owner-review-days">员工确认期限（天）</Label>
                <HelpTip
                  label="员工确认期限说明"
                  content="可设置 1–90 天。超期未确认的初始画像可由管理员批量送审。"
                />
                <Input
                  id="owner-review-days"
                  className="h-8 w-24"
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
              </div>
            </DraftField>
          </SectionCard>
          <SectionCard className="space-y-3 p-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">按能力类别设置</h2>
              <HelpTip
                label="类别例外说明"
                content="为特定类别单独设置审批策略，其余类别跟随默认规则。"
              />
            </div>
            <DraftField changed={suggestion.changed("category_overrides")}>
              {policy.data.categories.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  当前词典没有能力类别，使用全企业设置。
                </p>
              )}
              <div className="space-y-2">
                {policy.data.categories.map((category, index) => (
                  <div
                    key={category}
                    className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1.6fr)] items-center gap-2 rounded-md border bg-muted/30 px-2 py-1.5"
                  >
                    <Label
                      htmlFor={`policy-category-${index}`}
                      className="wrap-anywhere"
                    >
                      {category}
                    </Label>
                    <span aria-hidden="true" className="text-sm text-primary">
                      →
                    </span>
                    <Select
                      disabled={save.isPending || suggestion.blocked}
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
                        setChanges({
                          ...current,
                          category_overrides: overrides,
                        })
                      }}
                    >
                      <SelectTrigger
                        id={`policy-category-${index}`}
                        size="sm"
                        className="w-full min-w-0 bg-surface"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inherit">跟随默认</SelectItem>
                        <SelectItem value="required">经理审批</SelectItem>
                        <SelectItem value="optional">员工确认后发布</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                ))}
              </div>
            </DraftField>
          </SectionCard>
          <SetupError error={save.error} />
          <Button
            size="sm"
            type="submit"
            disabled={suggestion.blocked || !changes}
          >
            {save.isPending ? "正在保存…" : "保存审批规则"}
          </Button>
        </fieldset>
        {save.isSuccess && !changes && <p role="status">审批规则已保存</p>}
      </form>
    </RulesLayout>
  )
}
