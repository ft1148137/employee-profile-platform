import type { ChangeSetPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { ChangeSetCard } from "./ChangeSetCard"

export function PageDraft({
  orgId,
  draft,
  error,
}: {
  orgId: string
  draft?: ChangeSetPublic
  error: Error | null
}) {
  return (
    <>
      <SetupError error={error} />
      {draft && (
        <div className="space-y-3">
          <p className="text-sm">
            表单正在显示助手草稿，请核对后确认或放弃。确认会应用这份草稿中的全部{" "}
            {draft.items.length} 项改动。
          </p>
          <ChangeSetCard
            key={draft.id}
            orgId={orgId}
            changeSet={draft}
            disabled={false}
            inline
          />
        </div>
      )}
    </>
  )
}
