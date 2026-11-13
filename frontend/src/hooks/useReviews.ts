import { useQuery } from "@tanstack/react-query"
import { ReviewsService } from "@/client"
import { useOrg } from "./useOrg"

export function useReviews() {
  const { org } = useOrg()
  return useQuery({
    queryKey: ["reviews", org?.id],
    queryFn: async ({ signal }) =>
      (
        await ReviewsService.listPendingReviews({
          path: { org_id: org!.id },
          signal,
        })
      ).data,
    enabled: !!org,
    refetchInterval: 30000,
  })
}
