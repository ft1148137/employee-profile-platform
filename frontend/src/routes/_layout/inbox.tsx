import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Inbox } from "lucide-react"
import { useState } from "react"
import { EmployeeRequestsService, type InboxKind } from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/inbox")({
  component: InboxPage,
  head: () => ({ meta: [{ title: "收件箱 - 员工画像平台" }] }),
})

const kindLabels: Record<InboxKind, string> = {
  initial_review: "初始画像待过目",
  question: "待确认问题",
  submitted: "提交通知",
  status_changed: "请求状态变化",
  dictionary_changed: "能力词典已更新",
  review_requested: "画像请求待审批",
  decided: "画像审批结果",
}

function QuestionAnswer({
  orgId,
  questionId,
}: {
  orgId: string
  questionId: string
}) {
  const cache = useQueryClient()
  const [answer, setAnswer] = useState("")
  const [error, setError] = useState("")
  const query = useQuery({
    queryKey: ["question", orgId, questionId],
    queryFn: async () =>
      (
        await EmployeeRequestsService.readQuestion({
          path: { org_id: orgId, question_id: questionId },
        })
      ).data,
    retry: false,
  })
  const mutation = useMutation({
    mutationFn: async () =>
      (
        await EmployeeRequestsService.answerQuestion({
          path: { org_id: orgId, question_id: questionId },
          body: { answer: answer.trim() },
        })
      ).data,
    onSuccess: (data) => {
      cache.setQueryData(["question", orgId, questionId], data)
      cache.invalidateQueries({ queryKey: ["inbox", orgId] })
      cache.invalidateQueries({ queryKey: ["evidence", orgId] })
      setAnswer("")
      setError("")
    },
    onError: (err) => handleError.call(setError, err),
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  if (query.data.status !== "open")
    return (
      <p role="status">
        {query.data.status === "answered"
          ? "已回答，已保存为受限自述证据"
          : "问题已关闭"}
      </p>
    )
  const inputId = `answer-${questionId}`
  return (
    <form
      className="mt-4 space-y-3 border-t pt-4"
      onSubmit={(event) => {
        event.preventDefault()
        setError("")
        mutation.mutate()
      }}
    >
      <p className="text-sm text-muted-foreground">
        提问原因：{query.data.reason}
      </p>
      <label htmlFor={inputId} className="block text-sm font-medium">
        你的回答
      </label>
      <textarea
        id={inputId}
        className="min-h-28 w-full rounded-md border bg-background p-3 text-sm"
        required
        maxLength={10000}
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
        disabled={mutation.isPending}
      />
      <p className="text-xs text-muted-foreground">
        确认后保存为受限自述证据。回答不会直接修改画像；需要修改时，请到画像页起草并提交请求。
      </p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" disabled={!answer.trim() || mutation.isPending}>
        {mutation.isPending ? "正在保存…" : "确认回答"}
      </Button>
    </form>
  )
}

function InboxPage() {
  const { org, employeeId } = useOrg()
  if (!org) return <OrgState />
  if (!employeeId) return <p>当前组织尚未为你分配员工身份，请联系管理员。</p>
  return (
    <InboxContent
      key={`${org.id}:${employeeId}`}
      orgId={org.id}
      employeeId={employeeId}
    />
  )
}

function InboxContent({
  orgId,
  employeeId,
}: {
  orgId: string
  employeeId: string
}) {
  const [skip, setSkip] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState("")
  const query = useInbox(skip)
  const cache = useQueryClient()
  const read = useMutation({
    mutationFn: (id: string) =>
      EmployeeRequestsService.readInboxItem({
        path: { org_id: orgId, inbox_id: id },
      }),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: ["inbox", orgId] })
      setError("")
    },
    onError: (err) => handleError.call(setError, err),
  })
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex items-start gap-4 border-b pb-6">
        <Inbox className="mt-1 size-7 text-primary" aria-hidden="true" />
        <div>
          <h1 className="text-2xl font-semibold">收件箱</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            过目初始画像，补充本人经历，查看请求进展。
          </p>
        </div>
      </header>
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <>
          <p role="status" className="text-sm text-muted-foreground">
            {query.data.unread_count} 条未读 · {query.data.pending_count} 条待办
          </p>
          {!query.data.data.length && (
            <p className="rounded-xl border border-dashed p-8 text-center text-muted-foreground">
              收件箱暂时没有消息。
            </p>
          )}
          <ul className="space-y-4">
            {query.data.data.map((item) => (
              <li
                key={item.id}
                className={`min-w-0 rounded-xl border p-4 sm:p-6 ${!item.read_at ? "border-primary/30 bg-primary/5" : "bg-card"}`}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {kindLabels[item.kind]}
                  </span>
                  <time>{dateLabel(item.created_at)}</time>
                  {!item.read_at && <span className="text-primary">未读</span>}
                  {item.done_at && <span>已完成</span>}
                </div>
                <p className="my-3 break-words text-sm leading-relaxed">
                  {item.message}
                </p>
                <div className="flex flex-wrap gap-2">
                  {item.question_id && (
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSelected(selected === item.id ? null : item.id)
                        if (!item.read_at) read.mutate(item.id)
                      }}
                    >
                      {selected === item.id
                        ? "收起问题"
                        : item.done_at
                          ? "查看回答状态"
                          : "回答问题"}
                    </Button>
                  )}
                  {item.request_id && (
                    <Button variant="outline" asChild>
                      <Link
                        to="/employees/$employeeId"
                        params={{ employeeId }}
                        onClick={() => {
                          if (!item.read_at) read.mutate(item.id)
                        }}
                      >
                        查看画像请求
                      </Link>
                    </Button>
                  )}
                  {!item.read_at && (
                    <Button
                      variant="ghost"
                      disabled={read.isPending}
                      onClick={() => read.mutate(item.id)}
                    >
                      标为已读
                    </Button>
                  )}
                </div>
                {selected === item.id && item.question_id && (
                  <QuestionAnswer orgId={orgId} questionId={item.question_id} />
                )}
              </li>
            ))}
          </ul>
          {query.data.count > 20 && (
            <nav
              aria-label="收件箱分页"
              className="flex items-center justify-between gap-2"
            >
              <Button
                variant="outline"
                disabled={skip === 0}
                onClick={() => {
                  setSkip(skip - 20)
                  setSelected(null)
                }}
              >
                上一页
              </Button>
              <span className="text-sm">
                第 {skip / 20 + 1} 页 · 共 {query.data.count} 条
              </span>
              <Button
                variant="outline"
                disabled={skip + 20 >= query.data.count}
                onClick={() => {
                  setSkip(skip + 20)
                  setSelected(null)
                }}
              >
                下一页
              </Button>
            </nav>
          )}
        </>
      )}
    </div>
  )
}
