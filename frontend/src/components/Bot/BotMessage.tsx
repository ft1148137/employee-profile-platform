import { Link } from "@tanstack/react-router"
import { useState } from "react"
import type { QuestionAction, RequestAction, TurnResponse } from "@/client"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { Button } from "@/components/ui/button"

export type BotAction = RequestAction | QuestionAction
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
  disabled,
  onAction,
}: {
  message: TurnResponse["messages"][number]
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
      <Link to="/reviews" className="underline">
        去审批页
      </Link>
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
                  {item.before?.level ?? "未给等级"}
                </p>
                <p>
                  修改后：{item.after.after?.statement ?? "撤回此判断"} · 等级：
                  {item.after.after?.level ?? "未给等级"}
                </p>
                {item.after.after && (
                  <StatusBadge
                    kind="assessment_status"
                    value={item.after.after.assessment_status}
                  />
                )}
                <p>
                  依据说明：
                  {item.after.explanation ?? "暂无说明"}
                </p>
                {item.after.owner_statement && (
                  <p>本人补充：{item.after.owner_statement}</p>
                )}
                {item.after.uncertainty && (
                  <p>不确定之处：{item.after.uncertainty}</p>
                )}
                {item.after.objection && <p>异议：{item.after.objection}</p>}
                {item.warning && (
                  <p className="text-amber-700 dark:text-amber-300">
                    {item.warning}
                  </p>
                )}
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
