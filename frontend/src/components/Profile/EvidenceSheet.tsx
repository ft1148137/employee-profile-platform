import { useQuery } from "@tanstack/react-query"
import { LockKeyhole } from "lucide-react"
import { type EvidenceDetail, EvidenceService } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { relationLabels } from "./EvidenceList"
import { dateLabel, named, record, text } from "./fields"
import { labels } from "./labels"
import { ProfileError, ProfileLoading } from "./QueryState"
import { StatusBadge } from "./StatusBadge"

function EvidenceContent({ evidence }: { evidence: EvidenceDetail }) {
  const content = record(evidence.content) ? evidence.content : {}
  if (evidence.redacted)
    return (
      <p className="flex gap-2 rounded-lg border bg-muted/30 p-4 text-sm">
        <LockKeyhole className="size-5 shrink-0" />
        受限内容：仅本人和管辖该部门的经理可见
      </p>
    )
  if (evidence.kind === "work_record") {
    const url = evidence.task?.url
    const safeUrl = url && /^https?:\/\//i.test(url) ? url : null
    return (
      <div className="space-y-3">
        <p>
          {safeUrl ? (
            <a
              className="text-primary underline"
              href={safeUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              {evidence.source_ref}
            </a>
          ) : (
            evidence.source_ref
          )}
        </p>
        <h3 className="font-medium">{text(content.summary)}</h3>
        <p className="text-sm text-muted-foreground">
          类型：{named(content.issuetype)} · 状态：{named(content.status)}
        </p>
      </div>
    )
  }
  if (evidence.kind === "self_report")
    return (
      <div className="space-y-3">
        <p className="whitespace-pre-wrap leading-relaxed">
          {text(content.text ?? content.statement)}
        </p>
        <p className="text-sm text-muted-foreground">
          关联任务：
          {Array.isArray(content.related_task_keys)
            ? content.related_task_keys
                .filter((key: unknown) => typeof key === "string")
                .join("、") || "—"
            : "—"}
        </p>
      </div>
    )
  const answers: Record<string, { label: string }> = labels.review_answer
  return (
    <dl className="space-y-3">
      <div>
        <dt className="text-sm text-muted-foreground">问题</dt>
        <dd>{text(content.question)}</dd>
      </div>
      <div>
        <dt className="text-sm text-muted-foreground">回答</dt>
        <dd>{answers[text(content.answer)]?.label ?? text(content.answer)}</dd>
      </div>
      <div>
        <dt className="text-sm text-muted-foreground">评价</dt>
        <dd className="whitespace-pre-wrap">{text(content.comment)}</dd>
      </div>
    </dl>
  )
}

export function EvidenceSheet({
  orgId,
  evidenceId,
  onClose,
}: {
  orgId: string
  evidenceId: string | null
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ["evidence-detail", orgId, evidenceId],
    queryFn: async () =>
      (
        await EvidenceService.readEvidence({
          path: { org_id: orgId, evidence_id: evidenceId! },
        })
      ).data,
    enabled: evidenceId !== null,
  })
  const evidence = query.data
  return (
    <Sheet
      open={evidenceId !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{evidence?.title ?? "证据详情"}</SheetTitle>
          <SheetDescription>查看事实来源与能力判断的引用关系</SheetDescription>
        </SheetHeader>
        <div className="space-y-6 px-6 pb-8">
          {query.isPending ? (
            <ProfileLoading />
          ) : query.isError ? (
            <ProfileError retry={() => query.refetch()} />
          ) : (
            evidence && (
              <>
                <div className="flex items-center gap-3">
                  <StatusBadge kind="evidence_kind" value={evidence.kind} />
                  <span className="text-sm text-muted-foreground">
                    {dateLabel(evidence.occurred_at)} ·{" "}
                    {
                      {
                        active: "有效",
                        retracted: "已撤销",
                        superseded: "已更正",
                      }[evidence.status]
                    }
                  </span>
                </div>
                <EvidenceContent evidence={evidence} />
                <section className="space-y-3 border-t pt-5">
                  <h3 className="font-semibold">来源与导入信息</h3>
                  <p className="break-words text-sm">
                    {evidence.source} · {evidence.source_ref}
                  </p>
                  <pre className="whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-4 text-xs">
                    {JSON.stringify(evidence.provenance, null, 2)}
                  </pre>
                </section>
                <section className="space-y-3 border-t pt-5">
                  <h3 className="font-semibold">引用这条证据的能力判断</h3>
                  {evidence.claims.length ? (
                    <ul className="space-y-2 text-sm">
                      {evidence.claims.map((claim) => (
                        <li key={claim.claim_id}>
                          {claim.capability_name} ·{" "}
                          {relationLabels[claim.relation]}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">暂无引用。</p>
                  )}
                </section>
              </>
            )
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
