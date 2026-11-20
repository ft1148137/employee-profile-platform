import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowLeft, CheckCheck, ListFilter } from "lucide-react"
import { useRef, useState } from "react"
import {
  EmployeeRequestsService,
  type InboxKind,
  type InboxPublic,
} from "@/client"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import {
  SectionCard,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
import { dateLabel } from "@/components/Profile/fields"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/inbox")({
  component: InboxPage,
  head: () => ({ meta: [{ title: "收件箱 - 员工画像平台" }] }),
})

const kindLabels: Record<InboxKind, string> = {
  capacity: "协作与负荷",
  profile_edited: "画像已更新",
  initial_review: "初始画像待确认",
  question: "待确认问题",
  submitted: "画像申请已提交",
  status_changed: "申请状态更新",
  admin_todos: "管理员待办",
  dictionary_changed: "能力词典已更新",
  review_requested: "画像待审批",
  decided: "画像审批结果",
}

const trackingLink =
  /\n\/capacity\?tab=incoming&demand=([0-9a-f-]{36})&plans=1$/
const borrowLink = /\n\/capacity\?tab=borrows$/

function capacityDestination(message: string) {
  const tracking = message.match(trackingLink)
  if (tracking)
    return {
      to: "/capacity",
      search: { tab: "incoming", demand: tracking[1], plans: 1 },
      label: "打开方案页",
    } as const
  if (borrowLink.test(message))
    return {
      to: "/capacity",
      search: { tab: "borrows" },
      label: "打开借调页",
    } as const
  if (["借调请求", "借调请求待你确认"].includes(message))
    return {
      to: "/reviews",
      search: { type: "borrow" },
      label: "查看待审批",
    } as const
  if (["有负荷调整申请等你审批", "有延期申请等你审批"].includes(message))
    return {
      to: "/reviews",
      search: { type: "workload" },
      label: "查看待审批",
    } as const
  return { to: "/capacity", search: {}, label: "查看详情" } as const
}

function CapacityNoticeLink({
  message,
  onClick,
}: {
  message: string
  onClick: () => void
}) {
  const { label, ...link } = capacityDestination(message)
  return (
    <Button variant="outline" asChild>
      <Link {...link} onClick={onClick}>
        {label}
      </Link>
    </Button>
  )
}

const quickReplies = [
  "情况属实，我独立负责了相关工作",
  "仅参与协作支持，非主要负责人",
  "目前暂无补充，后续在画像页更新",
]

function messageText(item: InboxPublic) {
  if (item.kind === "capacity")
    return item.message.replace(trackingLink, "").replace(borrowLink, "")
  if (item.kind === "admin_todos")
    return item.message.replace(
      /^(你有 \d+ 条管理员待办，)请在网页首页处理。$/,
      "$1请前往网页「协同 → AI 助手（企业管理）」处理。",
    )
  return item.message
}

function KindBadge({ kind }: { kind: InboxKind }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-xs leading-none",
        kind === "question" || kind === "initial_review"
          ? "border-primary/20 bg-primary/10 text-primary"
          : "border-surface-border bg-muted text-muted-foreground",
      )}
    >
      {kindLabels[kind]}
    </span>
  )
}

function QuestionAnswer({
  orgId,
  questionId,
  onAnswerStart,
}: {
  orgId: string
  questionId: string
  onAnswerStart: () => void
}) {
  const cache = useQueryClient()
  const [answer, setAnswer] = useState("")
  const input = useRef<HTMLTextAreaElement>(null)
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
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (!answer.trim() || mutation.isPending) return
        setError("")
        onAnswerStart()
        mutation.mutate()
      }}
    >
      <p className="text-sm text-muted-foreground">
        提问原因：{query.data.reason}
      </p>
      <fieldset className="flex min-w-0 flex-wrap gap-1.5">
        <legend className="sr-only">快捷答复</legend>
        {quickReplies.map((reply) => (
          <Button
            key={reply}
            type="button"
            variant="outline"
            size="sm"
            className="h-auto max-w-full rounded-full py-1 whitespace-normal text-left text-xs shadow-none aria-pressed:border-primary aria-pressed:bg-primary/10 aria-pressed:text-primary aria-pressed:hover:bg-primary/10"
            aria-pressed={answer === reply}
            disabled={mutation.isPending}
            onClick={() => {
              setAnswer(reply)
              input.current?.focus()
            }}
          >
            {reply}
          </Button>
        ))}
      </fieldset>
      <label htmlFor={inputId} className="block text-sm font-medium">
        你的回答
      </label>
      <textarea
        id={inputId}
        ref={input}
        className="min-h-20 w-full rounded-md border bg-background p-2.5 text-sm"
        required
        maxLength={10000}
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
        disabled={mutation.isPending}
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          提交后保存为受限自述证据。
        </p>
        <Button
          size="sm"
          type="submit"
          disabled={!answer.trim() || mutation.isPending}
        >
          {mutation.isPending ? "正在保存…" : "提交回答"}
        </Button>
      </div>
    </form>
  )
}

function InboxPage() {
  const { org, employeeId } = useOrg()
  if (!org) return <OrgState />
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
  employeeId: string | null | undefined
}) {
  const [skip, setSkip] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [pendingOnly, setPendingOnly] = useState(false)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [kind, setKind] = useState("all")
  const [error, setError] = useState("")
  const query = useInbox(skip)
  const cache = useQueryClient()
  const read = useMutation({
    mutationFn: async (ids: string[]) => {
      const results = await Promise.allSettled(
        ids.map((id) =>
          EmployeeRequestsService.readInboxItem({
            path: { org_id: orgId, inbox_id: id },
          }),
        ),
      )
      const failed = results.find((result) => result.status === "rejected")
      if (failed) throw failed.reason
    },
    onMutate: () => setError(""),
    onSettled: () => cache.invalidateQueries({ queryKey: ["inbox", orgId] }),
    onError: (err) => handleError.call(setError, err),
  })
  const page = query.data?.data ?? []
  const rows = page.filter(
    (item) =>
      (!pendingOnly ||
        (!item.done_at &&
          ["initial_review", "question", "review_requested"].includes(
            item.kind,
          ))) &&
      (!unreadOnly || !item.read_at) &&
      (kind === "all" || item.kind === kind),
  )
  const unreadIds = page.filter((item) => !item.read_at).map((item) => item.id)
  const item = selected ? page.find((row) => row.id === selected) : rows[0]
  const markRead = () => {
    if (item && !item.read_at && !read.isPending) read.mutate([item.id])
  }
  return (
    <WorkspacePage
      variant="split"
      className="grid items-start md:grid-cols-[minmax(0,min(360px,44%))_minmax(0,1fr)]"
    >
      <h1 className="sr-only">收件箱</h1>
      <TopbarActions>
        <Button
          variant="outline"
          size="sm"
          title="标记当前页的未读消息"
          disabled={!unreadIds.length || read.isPending}
          onClick={() => read.mutate(unreadIds)}
        >
          <CheckCheck aria-hidden="true" /> 全部标为已读
        </Button>
      </TopbarActions>
      <p role="status" className="sr-only">
        {query.data?.unread_count ?? 0} 条未读 ·{" "}
        {query.data?.pending_count ?? 0} 条待办
      </p>
      {error && (
        <p
          role="alert"
          className="px-3 py-2 text-sm text-destructive md:col-span-2"
        >
          {error}
        </p>
      )}
      <aside
        aria-label="消息列表"
        className={cn(
          "min-w-0 border-surface-border bg-surface md:h-[calc(100dvh-3rem)] md:overflow-y-auto md:border-r",
          item && selected && "hidden md:block",
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-surface-border border-b px-3 py-2.5">
          <fieldset className="inline-flex min-w-0 gap-0.5 rounded-md bg-muted p-0.5">
            <legend className="sr-only">收件箱视图</legend>
            {[true, false].map((pending) => (
              <Button
                key={String(pending)}
                variant="ghost"
                size="sm"
                className="h-7 aria-pressed:bg-surface aria-pressed:shadow-xs"
                aria-pressed={pendingOnly === pending}
                onClick={() => {
                  setPendingOnly(pending)
                  setSelected(null)
                  setSkip(0)
                }}
              >
                {pending ? "待办" : "全部"}
                <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums">
                  {(pending ? query.data?.pending_count : query.data?.count) ??
                    0}
                </span>
              </Button>
            ))}
          </fieldset>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-7",
                  (unreadOnly || kind !== "all") && "text-primary",
                )}
              >
                <ListFilter aria-hidden="true" /> 筛选
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuCheckboxItem
                checked={unreadOnly}
                onCheckedChange={(checked) => {
                  setUnreadOnly(checked)
                  setSelected(null)
                  setSkip(0)
                }}
              >
                仅看未读
              </DropdownMenuCheckboxItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>按消息类型</DropdownMenuLabel>
              <DropdownMenuRadioGroup
                value={kind}
                onValueChange={(value) => {
                  setKind(value)
                  setSelected(null)
                  setSkip(0)
                }}
              >
                <DropdownMenuRadioItem value="all">
                  全部类型
                </DropdownMenuRadioItem>
                {Object.entries(kindLabels).map(([value, label]) => (
                  <DropdownMenuRadioItem key={value} value={value}>
                    {label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {query.isPending ? (
          <ProfileLoading />
        ) : query.isError ? (
          <ProfileError retry={() => query.refetch()} />
        ) : (
          <>
            {!rows.length && (
              <p className="p-6 text-center text-sm text-muted-foreground">
                {page.length ? "本页没有符合筛选的消息" : "暂无消息"}
              </p>
            )}
            <ul className="divide-y divide-surface-border border-surface-border border-b">
              {rows.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    aria-current={item?.id === row.id ? "true" : undefined}
                    onClick={() => setSelected(row.id)}
                    className="flex w-full min-w-0 items-start gap-2.5 px-3 py-2.5 text-left hover:bg-muted/70 focus-visible:outline-primary aria-current:bg-primary/5"
                  >
                    <span
                      className={cn(
                        "mt-1.5 size-2 shrink-0 rounded-full",
                        !row.read_at && "bg-blue-600",
                      )}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="flex flex-wrap items-center justify-between gap-1">
                        <KindBadge kind={row.kind} />
                        <time
                          dateTime={row.created_at}
                          className="text-xs text-muted-foreground"
                        >
                          {dateLabel(row.created_at)}
                        </time>
                      </span>
                      {!row.read_at && <span className="sr-only">未读</span>}
                      <span
                        className={cn(
                          "block truncate text-sm",
                          !row.read_at && "font-semibold",
                        )}
                      >
                        {messageText(row)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {query.data.count > 20 && (
              <nav
                aria-label="收件箱分页"
                className="flex flex-wrap items-center justify-between gap-1 p-2 text-xs"
              >
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={skip === 0}
                  onClick={() => {
                    setSkip(skip - 20)
                    setSelected(null)
                  }}
                >
                  上一页
                </Button>
                第 {skip / 20 + 1} 页 · 共 {query.data.count} 条
                <Button
                  variant="ghost"
                  size="sm"
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
      </aside>
      <div
        className={cn(
          "min-w-0 p-3 md:max-h-[calc(100dvh-3rem)] md:overflow-y-auto md:p-4",
          (!item || !selected) && "hidden md:block",
        )}
      >
        {item && (
          <>
            <Button
              className="mb-2 md:hidden"
              variant="ghost"
              size="sm"
              onClick={() => setSelected(null)}
            >
              <ArrowLeft aria-hidden="true" /> 返回消息列表
            </Button>
            <SectionCard aria-label="消息详情" className="p-0">
              <header className="flex flex-wrap items-center gap-2 border-surface-border border-b px-3 py-2 sm:px-4">
                <KindBadge kind={item.kind} />
                <time
                  dateTime={item.created_at}
                  className="text-xs text-muted-foreground"
                >
                  {dateLabel(item.created_at)}
                </time>
                {!item.read_at && (
                  <Button
                    className="ml-auto"
                    variant="ghost"
                    size="sm"
                    disabled={read.isPending}
                    onClick={markRead}
                  >
                    标为已读
                  </Button>
                )}
              </header>
              <h2 className="whitespace-pre-wrap wrap-anywhere px-3 py-3 text-base font-semibold sm:px-4">
                {messageText(item)}
              </h2>
              {(item.question_id ||
                item.kind === "capacity" ||
                (item.request_id &&
                  (item.kind === "review_requested"
                    ? !item.done_at
                    : employeeId))) && (
                <div className="border-surface-border border-t p-3 sm:p-4">
                  {item.question_id && (
                    <QuestionAnswer
                      key={item.question_id}
                      orgId={orgId}
                      questionId={item.question_id}
                      onAnswerStart={() => setSelected(item.id)}
                    />
                  )}
                  {item.request_id &&
                    item.kind === "review_requested" &&
                    !item.done_at && (
                      <Button variant="outline" size="sm" asChild>
                        <Link
                          to="/reviews/$requestId"
                          params={{ requestId: item.request_id }}
                          onClick={markRead}
                        >
                          查看并审批
                        </Link>
                      </Button>
                    )}
                  {item.request_id &&
                    employeeId &&
                    item.kind !== "review_requested" && (
                      <Button variant="outline" size="sm" asChild>
                        <Link
                          to="/employees/$employeeId"
                          params={{ employeeId }}
                          onClick={markRead}
                        >
                          查看详情
                        </Link>
                      </Button>
                    )}
                  {item.kind === "capacity" && (
                    <CapacityNoticeLink
                      message={item.message}
                      onClick={markRead}
                    />
                  )}
                </div>
              )}
            </SectionCard>
          </>
        )}
      </div>
    </WorkspacePage>
  )
}
