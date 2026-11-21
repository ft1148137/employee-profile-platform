import { Link } from "@tanstack/react-router"
import { useState } from "react"
import type {
  CapabilitiesPublic,
  CapacityAction,
  Claim,
  PlanCard,
  ProfileEditAction,
  ProjectDraftAction,
  QuestionAction,
  RequestAction,
  ReviewAction,
  TurnResponse,
} from "@/client"
import { levelLabel } from "@/components/Profile/fields"
import { RequestReviewers } from "@/components/Profile/RequestReviewers"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { Button } from "@/components/ui/button"
import { safeNext } from "@/utils"
import { ProjectDraftMessage } from "./ProjectDraftMessage"
import { previewHistoryLabel, type RequestCard } from "./requestCards"

export type BotAction =
  | RequestAction
  | QuestionAction
  | ReviewAction
  | ProfileEditAction
  | CapacityAction
  | ProjectDraftAction
const actionLabels = {
  confirm_submit: "确认提交",
  continue_editing: "继续修改",
  withdraw: "撤回",
}
const statuses: Record<string, string> = {
  draft: "草稿",
  pending_owner: "待本人确认",
  pending_review: "待经理审批",
  withdrawn: "已撤回",
  approved: "已批准",
  changes_requested: "已退回",
  rejected: "已拒绝",
}

function assistantText(text: string) {
  const detailLinks =
    /(?:\[([^\]\n]+)\]\()?((?<![\w:/=&.-])\/(employees\/|capacity\?tab=projects&project=)([\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}))(?![\w/-])\)?/g
  let cursor = 0
  const parts = Array.from(text.matchAll(detailLinks)).flatMap((match) => {
    const [whole, label, path, prefix, id] = match
    const before = text.slice(cursor, match.index)
    cursor = match.index + (label ? whole.length : path.length)
    const link =
      prefix === "employees/" ? (
        <Link
          key={match.index}
          to="/employees/$employeeId"
          params={{ employeeId: id }}
          className="underline"
        >
          {label ?? path}
        </Link>
      ) : (
        <Link
          key={match.index}
          to="/capacity"
          search={{ tab: "projects", project: id }}
          className="underline"
        >
          {label ?? path}
        </Link>
      )
    return [before, link]
  })
  return parts.concat(text.slice(cursor))
}

export function BotMessage({
  message,
  dictionary,
  laterRequest,
  disabled,
  onAction,
}: {
  message: TurnResponse["messages"][number]
  dictionary?: CapabilitiesPublic
  laterRequest?: RequestCard
  disabled: boolean
  onAction: (action: BotAction) => void
}) {
  const [answer, setAnswer] = useState("")
  if (message.type === "project_draft_card")
    return (
      <ProjectDraftMessage
        card={message}
        disabled={disabled}
        onConfirm={onAction}
      />
    )
  const displayLevel = (claim: Claim | null | undefined, key: string) =>
    claim?.level_name ??
    (claim?.level
      ? dictionary
        ? levelLabel(
            dictionary.data.find((capability) => capability.key === key)
              ?.level_rubric,
            claim.level,
          )
        : "等级已调整"
      : "未定级")
  if (message.type === "capacity_card") {
    const link = message.detail_link
    return (
      <section
        className="space-y-4 rounded-xl border bg-background p-4"
        aria-label={message.title}
      >
        <h3 className="font-semibold">{message.title}</h3>
        {!!message.questions?.length && (
          <div className="rounded-md bg-amber-50 p-3 text-amber-950">
            <p className="font-medium">确认操作</p>
            {message.questions?.map((question) => (
              <p key={question}>{question}</p>
            ))}
          </div>
        )}
        {message.fields?.length ? (
          <>
            <dl className="space-y-2">
              {message.fields?.map((field) => (
                <div
                  key={field.label}
                  className={field.unclear ? "rounded-md bg-amber-50 p-2" : ""}
                >
                  <dt className="inline font-medium">{field.label}：</dt>
                  <dd className="inline break-words">{field.value}</dd>
                  {field.unclear && (
                    <span className="ml-2 font-medium text-amber-900">
                      待确认
                    </span>
                  )}
                </div>
              ))}
            </dl>
            <p>{message.content.split("\n").slice(-1)[0]}</p>
          </>
        ) : (
          <p className="whitespace-pre-wrap break-words leading-7">
            {message.content}
          </p>
        )}
        {link && safeNext(link.href) === link.href && (
          <Link
            to={link.href}
            href={link.href}
            className="inline-block underline"
          >
            {link.label}
          </Link>
        )}
        {message.result && <p role="status">{message.result}</p>}
        {message.plan && <PlanDetails plan={message.plan} />}
        {message.status === "pending" && (
          <div className="flex flex-wrap gap-2">
            {(message.actions ?? []).map((action) => (
              <Button
                key={action.action}
                disabled={disabled}
                variant={
                  action.action === "confirm_capacity" ? "default" : "outline"
                }
                onClick={() => onAction(action)}
              >
                {action.action === "confirm_capacity" ? "确认" : "取消"}
              </Button>
            ))}
          </div>
        )}
      </section>
    )
  }
  if (message.type === "plan_card")
    return (
      <section
        className="space-y-4 rounded-xl border bg-background p-4"
        aria-label={message.title}
      >
        <PlanDetails plan={message} />
      </section>
    )
  if (message.type === "text")
    return (
      <div className="space-y-2">
        <p className="whitespace-pre-wrap break-words leading-7">
          {assistantText(message.text)}
        </p>
        {message.review_link && (
          <Link to="/reviews" className="underline">
            去审批页
          </Link>
        )}
        {message.capacity_links?.map((link) => (
          <a key={link.href} href={link.href} className="mr-4 underline">
            前往{link.label}
          </a>
        ))}
      </div>
    )
  if (message.type === "profile_edit_card")
    return (
      <section
        className="space-y-4 rounded-xl border bg-background p-4"
        aria-label="画像修改确认"
      >
        <h3 className="font-semibold">直接修改画像：{message.employee_name}</h3>
        {message.items.map((item) => {
          return (
            <div key={item.capability_key} className="space-y-2 border-t pt-3">
              <h4 className="font-medium">{item.capability_name}</h4>
              {[
                { label: "修改前", claim: item.before },
                { label: "修改后", claim: item.after },
              ].map(({ label, claim }) => (
                <p key={label}>
                  {label}：{claim?.statement ?? "暂无记录"} · 等级：
                  {displayLevel(claim, item.capability_key)}
                  {claim && (
                    <StatusBadge
                      kind="assessment_status"
                      value={claim.assessment_status}
                    />
                  )}
                </p>
              ))}
            </div>
          )
        })}
        <p>确认后将直接生效并通知本人。</p>
        <div className="flex flex-wrap gap-2">
          {(message.actions ?? []).map((action) => (
            <Button
              key={action.action}
              disabled={disabled}
              variant={
                action.action === "confirm_profile_edit" ? "default" : "outline"
              }
              onClick={() => onAction(action)}
            >
              {action.action === "confirm_profile_edit"
                ? "确认修改"
                : "继续修改"}
            </Button>
          ))}
        </div>
      </section>
    )
  if (message.type === "review_card")
    return (
      <section
        className="space-y-4 rounded-xl border bg-background p-4"
        aria-label="审批决定确认"
      >
        <h3 className="font-semibold">
          审批决定确认（第 {message.revision} 版）
        </h3>
        {message.items.map((item) => {
          const decision = message.decisions.find(
            (d) => d.item_id === item.after.id,
          )
          return (
            <div key={item.after.id} className="space-y-2 border-t pt-3">
              <h4 className="font-medium">{item.after.capability.name}</h4>
              <p>
                修改前：{item.before?.statement ?? "暂无记录"} · 等级：
                {displayLevel(item.before, item.after.capability.key)}
                {item.before && (
                  <StatusBadge
                    kind="assessment_status"
                    value={item.before.assessment_status}
                  />
                )}
              </p>
              <p>
                修改后：{item.after.after?.statement ?? "移除此项"} · 等级：
                {displayLevel(item.after.after, item.after.capability.key)}
                {item.after.after && (
                  <StatusBadge
                    kind="assessment_status"
                    value={item.after.after.assessment_status}
                  />
                )}
              </p>
              <p>
                相关记录：
                {item.evidence.map((e) => e.title).join("、") || "暂无"}
              </p>
              {item.jira_only && <p>仅来自 Jira，批准后为「暂定」</p>}
              <p>
                审批决定：{decision ? statuses[decision.decision] : "未填写"}
              </p>
              <p>说明：{decision?.reason || "未填写"}</p>
            </div>
          )
        })}
        <Link
          to="/reviews/$requestId"
          params={{ requestId: message.id }}
          className="underline"
        >
          去审批页查看证据
        </Link>
        <div className="flex flex-wrap gap-2">
          {(message.actions ?? []).map((action) => (
            <Button
              key={action.action}
              disabled={disabled}
              variant={
                action.action === "confirm_review" ? "default" : "outline"
              }
              onClick={() => onAction(action)}
            >
              {action.action === "confirm_review" ? "确认决定" : "继续修改"}
            </Button>
          ))}
        </div>
      </section>
    )
  const previewLabel =
    message.type === "preview_card"
      ? previewHistoryLabel(message, laterRequest)
      : undefined
  const currentStatus =
    laterRequest?.type === "status_card"
      ? laterRequest.status
      : message.type === "status_card"
        ? message.status
        : undefined
  const mixed =
    message.type === "status_card" &&
    (currentStatus === "pending_review" || currentStatus === "withdrawn") &&
    message.items.some((item) => item.decision === "approved") &&
    message.items.some((item) => item.decision === null)
  return (
    <section
      className="space-y-4 rounded-xl border bg-background p-4"
      aria-label={
        message.type === "preview_card"
          ? "变更预览"
          : message.type === "status_card"
            ? "请求进度"
            : "待确认问题"
      }
    >
      {message.type === "question_card" ? (
        <>
          <h3 className="font-semibold">{message.question}</h3>
          <p className="text-muted-foreground">{message.reason}</p>
          {message.actions.includes("answer_question") ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({
                  action: "answer_question",
                  question_id: message.id,
                  answer: answer.trim(),
                })
              }}
            >
              <label className="block space-y-2">
                你的回答
                <textarea
                  aria-label="你的回答"
                  className="min-h-24 w-full rounded-md border bg-background p-3"
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  maxLength={10000}
                  disabled={disabled}
                  required
                />
              </label>
              <p className="text-muted-foreground">
                确认后将作为受限自述证据保存。
              </p>
              <Button disabled={disabled || !answer.trim()}>提交回答</Button>
            </form>
          ) : (
            <p>已回答</p>
          )}
        </>
      ) : (
        <>
          <h3 className="font-semibold">
            {message.type === "preview_card"
              ? `变更预览${previewLabel ? ` · ${previewLabel}` : ""}`
              : laterRequest
                ? "历史请求 · 已过期"
                : (statuses[message.status] ?? "请求状态已更新")}
          </h3>
          {message.type === "preview_card" ? (
            !previewLabel && (
              <RequestReviewers
                reviewers={message.reviewers}
                requiresManager={message.requires_manager}
                partialAutoPublish={message.partial_auto_publish}
                preview
              />
            )
          ) : laterRequest && currentStatus ? (
            <p>当前状态：{statuses[currentStatus] ?? "请求状态已更新"}</p>
          ) : message.status === "pending_review" ? (
            <RequestReviewers reviewers={message.reviewers} />
          ) : null}
          {message.type === "preview_card" ? (
            message.items.map((item, index) => (
              <div key={index} className="space-y-2 border-t pt-3">
                <h4 className="font-medium">{item.after.capability.name}</h4>
                {item.submission_notice && <p>{item.submission_notice}</p>}
                <p>
                  修改前：{item.before?.statement ?? "暂无记录"} · 等级：
                  {displayLevel(item.before, item.after.capability.key)}
                </p>
                <p>
                  修改后：{item.after.after?.statement ?? "移除此项"} · 等级：
                  {displayLevel(item.after.after, item.after.capability.key)}
                </p>
              </div>
            ))
          ) : (
            <>
              {!laterRequest && message.status === "pending_review" && (
                <p>
                  {mixed
                    ? "部分能力已发布（暂定），其余待经理审批。"
                    : message.actions.length
                      ? "已提交审批，经理审批前可撤回。"
                      : "已提交审批。"}
                </p>
              )}
              {mixed && !laterRequest && message.actions.length > 0 && (
                <p>撤回仅取消待经理审批项，已发布项不受影响。</p>
              )}
              {message.items.map((item, index) => (
                <p key={index}>
                  {item.capability.name}：{item.after?.statement ?? "移除此项"}{" "}
                  · 等级：
                  {displayLevel(item.after, item.capability.key)}
                  {mixed &&
                    (item.decision === "approved" ||
                      item.decision === null) && (
                      <span className="ml-2 font-medium">
                        {item.decision === "approved"
                          ? "已发布（暂定）"
                          : currentStatus === "withdrawn"
                            ? "已撤回"
                            : "待经理审批"}
                      </span>
                    )}
                </p>
              ))}
            </>
          )}
          <div className="flex flex-wrap gap-2">
            {!laterRequest &&
              message.actions.map((action) => (
                <Button
                  key={action.action}
                  variant={
                    action.action === "confirm_submit" ? "default" : "outline"
                  }
                  disabled={disabled}
                  onClick={() => onAction(action)}
                >
                  {actionLabels[action.action]}
                </Button>
              ))}
          </div>
        </>
      )}
    </section>
  )
}

function PlanDetails({ plan }: { plan: PlanCard }) {
  return (
    <div className="space-y-4">
      <h3 className="font-semibold">{plan.title}</h3>
      <dl className="space-y-2">
        {plan.fields.map((field) => (
          <div key={field.label}>
            <dt className="inline font-medium">{field.label}：</dt>
            <dd className="inline break-words">{field.value}</dd>
          </div>
        ))}
      </dl>
      {plan.changes && <p>本次调整：{plan.changes}</p>}
      <a href={plan.link} className="underline">
        在方案页修改
      </a>
      {plan.rule_fallback && (
        <div className="space-y-3 border-t pt-3">
          <h4 className="font-semibold">备选方案</h4>
          <dl className="space-y-2">
            {plan.rule_fallback.fields.map((field) => (
              <div key={field.label}>
                <dt className="inline font-medium">{field.label}：</dt>
                <dd className="inline break-words">{field.value}</dd>
              </div>
            ))}
          </dl>
          <p className="font-medium">与推荐方案对比</p>
          {plan.rule_fallback.differences.map((field) => (
            <p key={field.label}>
              {field.label}：{field.value}
            </p>
          ))}
          <a href={plan.rule_fallback.link} className="underline">
            查看备选方案
          </a>
        </div>
      )}
    </div>
  )
}
