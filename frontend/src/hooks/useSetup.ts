import { useQuery } from "@tanstack/react-query"
import { SetupService } from "@/client"
import { useOrg } from "./useOrg"

export function useSetup() {
  const { org, roles } = useOrg()
  return useQuery({
    queryKey: ["setup", org?.id],
    queryFn: async () =>
      (await SetupService.readSetup({ path: { org_id: org!.id } })).data,
    enabled: !!org && roles.includes("org_admin"),
  })
}
