import { useQuery } from "@tanstack/react-query"
import { AdminAssistantService } from "@/client"
import { useOrg } from "./useOrg"

export function useAdminAvailability() {
  const { org, roles } = useOrg()
  return useQuery({
    queryKey: ["admin-assistant-availability", org?.id],
    queryFn: async () =>
      (
        await AdminAssistantService.assistantAvailability({
          query: { org_id: org!.id },
        })
      ).data,
    enabled: !!org && roles.includes("org_admin"),
  })
}
