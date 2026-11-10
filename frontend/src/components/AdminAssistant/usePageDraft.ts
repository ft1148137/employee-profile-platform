import { useQuery } from "@tanstack/react-query"
import { useEffect } from "react"
import { AdminAssistantService, type ChangeSetPublic } from "@/client"
import useAuth from "@/hooks/useAuth"
import { useCurrentConversation } from "@/hooks/useCurrentConversation"
import { useOrg } from "@/hooks/useOrg"

export function usePageDraft(
  orgId: string,
  functionName: string,
  reset?: (draft?: ChangeSetPublic) => void,
) {
  const { roles } = useOrg()
  const { user } = useAuth()
  const sessionId = useCurrentConversation(
    `admin-assistant:${user?.id}:${orgId}`,
  )
  const query = useQuery({
    queryKey: ["admin-page-drafts", orgId, functionName, sessionId],
    enabled: !!user && roles.includes("org_admin"),
    queryFn: async () =>
      (
        await AdminAssistantService.assistantListChangeSets({
          path: { org_id: orgId },
          query: {
            status: "draft",
            function: functionName,
            session_id: sessionId ?? undefined,
          },
        })
      ).data,
  })
  // shortcut: Show the newest matching draft; add a picker when comparing drafts is needed.
  const draft = query.data?.[0]
  // A change set may contain the same function twice; the last item is applied last.
  const item = draft?.items
    .slice()
    .reverse()
    .find((entry) => entry.function === functionName)
  useEffect(() => {
    reset?.(draft)
  }, [draft, reset])
  return {
    draft,
    item,
    error: query.error,
    blocked:
      roles.includes("org_admin") &&
      (query.isPending || query.isError || !!draft),
    values<T extends object>(saved: T): T {
      return { ...saved, ...item?.after }
    },
    changed(field: string) {
      return (
        !!item &&
        JSON.stringify(item.before?.[field]) !==
          JSON.stringify(item.after[field])
      )
    },
  }
}
