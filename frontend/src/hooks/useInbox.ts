import { useQuery } from "@tanstack/react-query"
import { EmployeeRequestsService } from "@/client"
import { useOrg } from "./useOrg"

export function useInbox(skip = 0) {
  const { org, employeeId } = useOrg()
  return useQuery({
    queryKey: ["inbox", org?.id, employeeId, skip],
    queryFn: async () =>
      (
        await EmployeeRequestsService.readInbox({
          path: { org_id: org!.id },
          query: { skip, limit: 20 },
        })
      ).data,
    enabled: !!org,
    retry: false,
    refetchInterval: 30000,
  })
}
