import { useQuery } from "@tanstack/react-query"
import { CapacityService, type DemandPublic } from "@/client"
import {
  useExtensions,
  useMyAssignments,
} from "@/components/Capacity/AssignmentFields"
import { useCapacitySettings } from "@/components/Capacity/shared"
import { useOrg } from "./useOrg"
import { useReviews } from "./useReviews"

export function useBorrowRequests(
  orgId: string,
  mine: boolean,
  enabled = true,
) {
  return useQuery({
    queryKey: ["capacity", orgId, "borrows", mine],
    queryFn: async ({ signal }) =>
      (
        await CapacityService.listBorrowRequests({
          path: { org_id: orgId },
          query: { mine },
          signal,
        })
      ).data,
    enabled,
  })
}

export function usePendingApprovals() {
  const { org, roles, employeeId } = useOrg()
  const orgId = org?.id ?? ""
  const manages = roles.includes("org_admin") || roles.includes("dept_manager")
  const capacity = useCapacitySettings(org?.id)
  const enabled = capacity.data?.enabled === true
  const profile = useReviews()
  const workloads = useQuery({
    queryKey: ["capacity", orgId, "requests"],
    queryFn: async ({ signal }) =>
      (
        await CapacityService.listWorkloadRequests({
          path: { org_id: orgId },
          signal,
        })
      ).data,
    enabled,
  })
  const extensions = useExtensions(orgId, enabled)
  const own = useMyAssignments(orgId, enabled && !!employeeId)
  const incoming = useQuery({
    queryKey: ["capacity", orgId, "demands", "incoming", "submitted"],
    enabled: enabled && manages,
    queryFn: async ({ signal }) => {
      const pending = new Map<string, DemandPublic>()
      const limit = 100
      for (let skip = 0; ; skip += limit) {
        const { data: page } = await CapacityService.listDemands({
          path: { org_id: orgId },
          query: { side: "incoming", status: "submitted", skip, limit },
          signal,
        })
        for (const row of page.data) {
          if (row.can_reply === true) pending.set(row.id, row)
        }
        if (skip + limit >= page.count) return [...pending.values()]
      }
    },
  })
  const selection = useBorrowRequests(orgId, false, enabled && manages)
  const consent = useBorrowRequests(orgId, true, enabled && !!employeeId)
  const pendingWorkloads = enabled
    ? (workloads.data?.data.filter((row) => row.status === "pending") ?? [])
    : []
  const ownIds = new Set(own.data?.data.map((item) => item.id))
  const pendingExtensions =
    enabled && (!employeeId || own.data)
      ? (extensions.data?.data.filter(
          (row) => row.status === "pending" && !ownIds.has(row.assignment_id),
        ) ?? [])
      : []
  const demands = enabled && manages ? (incoming.data ?? []) : []
  const borrows = new Map(
    (enabled && manages ? (selection.data ?? []) : [])
      .filter((row) => row.status === "pending_selection")
      .map((request) => [request.id, { request, mine: false }]),
  )
  if (enabled && employeeId) {
    for (const request of consent.data ?? []) {
      if (request.status === "pending_consent")
        borrows.set(request.id, { request, mine: true })
    }
  }
  const counts = {
    profile: profile.data?.count ?? 0,
    workload: pendingWorkloads.length + pendingExtensions.length,
    demand: demands.length,
    borrow: borrows.size,
  }
  return {
    capacity,
    profile,
    pendingWorkloads,
    pendingExtensions,
    demands,
    borrows: [...borrows.values()],
    workloadQueries: enabled
      ? [workloads, extensions, ...(employeeId ? [own] : [])]
      : [],
    demandQueries: enabled && manages ? [incoming] : [],
    borrowQueries: enabled
      ? [...(manages ? [selection] : []), ...(employeeId ? [consent] : [])]
      : [],
    counts,
    totalCount:
      counts.profile + counts.workload + counts.demand + counts.borrow,
  }
}
