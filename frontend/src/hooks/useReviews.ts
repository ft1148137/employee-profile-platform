import { useQuery } from "@tanstack/react-query"
import { ReviewsService } from "@/client"
import { useOrg } from "./useOrg"

export function useReviews() {
  const { org } = useOrg()
  return useQuery({
    queryKey: ["reviews", org?.id],
    queryFn: async () =>
      (await ReviewsService.listPendingReviews({ path: { org_id: org!.id } }))
        .data,
    enabled: !!org,
    refetchInterval: 30000,
  })
}
