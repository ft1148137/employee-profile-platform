import { useLocation } from "@tanstack/react-router"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export type AssistantMode = "admin" | "personal"

declare module "@tanstack/react-router" {
  interface HistoryState {
    assistantRestart?: number
  }
}

export function useAssistantWorkspace() {
  const { org, employeeId, roles } = useOrg()
  const { user } = useAuth()
  const { pathname, search } = useLocation()
  const isAdmin = roles.includes("org_admin")
  const requested = pathname === "/admin/assistant" ? "admin" : search.mode
  const mode =
    pathname === "/assistant" && search.draft
      ? "personal"
      : requested === "admin" || (isAdmin && !employeeId)
        ? "admin"
        : "personal"
  const storageKey =
    user && org
      ? mode === "admin"
        ? isAdmin
          ? `admin-assistant:${user.id}:${org.id}`
          : undefined
        : employeeId
          ? `bot-session:${user.id}:${org.id}:${employeeId}`
          : undefined
      : undefined

  return {
    org,
    employeeId,
    mode,
    canSwitch: isAdmin && !!employeeId,
    storageKey,
    searchMode:
      storageKey && (mode === "admin" || requested === "personal")
        ? mode
        : undefined,
  } as const
}
