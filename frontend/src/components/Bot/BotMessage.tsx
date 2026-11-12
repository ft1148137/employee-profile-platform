import { Link } from "@tanstack/react-router"
import { useState } from "react"
import type {
  CapabilitiesPublic,
  QuestionAction,
  RequestAction,
  ReviewAction,
  TurnResponse,
} from "@/client"
import { levelLabel } from "@/components/Profile/fields"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { Button } from "@/components/ui/button"

export type BotAction = RequestAction | QuestionAction | ReviewAction
const actionLabels = {
  confirm_submit: "确认提交",
  continue_editing: "继续修改",
  withdraw: "撤回",
}
const statuses: Record<string, string> = {
  draft: "草稿",
  pending_owner: "待本人过目",
  pending_review: "待经理审批",
  withdrawn: "已撤回",
  approved: "已批准",
  changes_requested: "已退回",
  rejected: "已拒绝",
}

export function BotMessage({
  message,
  dictionary,
  disabled,
  onAction,
}: {
  message: TurnResponse["messages"][number]
  dictionary?: CapabilitiesPublic
  disabled: boolean
  onAction: (action: BotAction) => void
}) {
  const [answer, setAnswer] = useState("")
  if (message.type === "text")
    return (
      <div className="space-y-2">
        <p className="whitespace-pre-wrap break-words leading-7">
          {message.text}
        </p>
        {message.review_link && (
          <Link to="/reviews" className="underline">
            去审批页
          </Link>
        )}
      </div>
    )
  if (message.type === "review_card")
    return (
      <section
        className="space-y-4 rounded-xl border bg-background p-4"
        aria-label="审批决定复述"
      >
        <h3 className="font-semibold">
          审批决定复述（修订 {message.revision}）
        </h3>
        {message.items.map((item) => {
          const rubric = dictionary?.data.find(
            (capability) => capability.key === item.after.capability.key,
          )?.level_rubric
          const decision = message.decisions.find(
            (d) => d.item_id === item.after.id,
          )
          return (
            <div key={item.after.id} className="space-y-2 border-t pt-3">
              <h4 className="font-medium">{item.after.capability.name}</h4>
              <p>
                修改前：{item.before?.statement ?? "暂无判断"} · 等级：
                {!dictionary && item.before?.level
                  ? "等级暂不可用"
                  : item.before?.level
                    ? levelLabel(rubric, item.before.level)
                    : "未给等级"}
                {item.before && (
                  <StatusBadge
                    kind="assessment_status"
                    value={item.before.assessment_status}
                  />
                )}
              </p>
              <p>
                修改后：{item.after.after?.statement ?? "撤回此判断"} · 等级：
                {!dictionary && item.after.after?.level
                  ? "等级暂不可用"
                  : item.after.after?.level
                    ? levelLabel(rubric, item.after.after.level)
                    : "未给等级"}
                {item.after.after && (
                  <StatusBadge
                    kind="assessment_status"
                    value={item.after.after.assessment_status}
                  />
                )}
              </p>
              <p>
                依据标题：
                {item.evidence.map((e) => e.title).join("、") || "暂无"}
              </p>
              {item.jira_only && <p>仅 Jira 来源（最多暂定）</p>}
              <p>决定：{decision ? statuses[decision.decision] : "未填写"}</p>
              <p>原因：{decision?.reason || "未填写"}</p>
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
  return (
    <section
      className="space-y-4 rounded-xl border bg-background p-4"
      aria-label={
        message.type === "preview_card"
          ? "提交前预览"
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
                确认后的回答
                <textarea
                  aria-label="确认后的回答"
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
              <Button disabled={disabled || !answer.trim()}>确认回答</Button>
            </form>
          ) : (
            <p>问题已处理</p>
          )}
        </>
      ) : (
        <>
          <h3 className="font-semibold">
            {message.type === "preview_card"
              ? "提交前预览"
              : (statuses[message.status] ?? "请求状态已更新")}
          </h3>
          {message.type === "preview_card" ? (
            message.items.map((item, index) => (
              <div key={index} className="space-y-2 border-t pt-3">
                <h4 className="font-medium">{item.after.capability.name}</h4>
                <p>
                  修改前：{item.before?.statement ?? "暂无判断"} · 等级：
                  {!dictionary && item.before?.level
                    ? "等级暂不可用"
                    : item.before?.level
                      ? levelLabel(
                          dictionary?.data.find(
                            (capability) =>
                              capability.key === item.after.capability.key,
                          )?.level_rubric,
                          item.before.level,
                        )
                      : "未给等级"}
                </p>
                <p>
                  修改后：{item.after.after?.statement ?? "撤回此判断"} · 等级：
                  {!dictionary && item.after.after?.level
                    ? "等级暂不可用"
                    : item.after.after?.level
                      ? levelLabel(
                          dictionary?.data.find(
                            (capability) =>
                              capability.key === item.after.capability.key,
                          )?.level_rubric,
                          item.after.after.level,
                        )
                      : "未给等级"}
                </p>
              </div>
            ))
          ) : (
            <>
              {message.status === "pending_review" && (
                <p>已提交给经理审批，审批前可以撤回。</p>
              )}
              {message.items.map((item, index) => (
                <p key={index}>
                  {item.capability.name}：
                  {item.after?.statement ?? "撤回此判断"}
                </p>
              ))}
            </>
          )}
          <div className="flex flex-wrap gap-2">
            {message.actions.map((action) => (
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
