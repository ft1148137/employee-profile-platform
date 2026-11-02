import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { MeService } from "@/client"
import useAuth from "./useAuth"

export function useOrg() {
  const { user } = useAuth()
  const client = useQueryClient()
  const selectionKey = ["currentOrgId", user?.id]
  const { data: selectedId } = useQuery({
    queryKey: selectionKey,
    queryFn: () => localStorage.getItem("current_org_id"),
    initialData: () => localStorage.getItem("current_org_id"),
    staleTime: Infinity,
  })
  const query = useQuery({
    queryKey: ["memberships", user?.id],
    queryFn: async () => (await MeService.readMemberships()).data,
    enabled: !!user,
  })
  const memberships = query.data ?? []
  const membership =
    memberships.find((item) => item.org.id === selectedId) ?? memberships[0]
  const org = membership?.org
  useEffect(() => {
    if (org) localStorage.setItem("current_org_id", org.id)
    else if (query.isSuccess) localStorage.removeItem("current_org_id")
  }, [org, query.isSuccess])
  return {
    ...query,
    isPending: !user || query.isPending,
    memberships,
    org,
    roles: membership?.roles ?? [],
    employeeId: membership?.employee_id,
    managedDepartmentIds: membership?.managed_department_ids ?? [],
    setOrg: (id: string) => {
      if (memberships.some((item) => item.org.id === id)) {
        localStorage.setItem("current_org_id", id)
        client.setQueryData(selectionKey, id)
      }
    },
  }
}
