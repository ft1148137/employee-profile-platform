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
            当前显示助手起草的内容（共 {draft.items.length}{" "}
            项改动），核对后可确认生效或放弃。
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
