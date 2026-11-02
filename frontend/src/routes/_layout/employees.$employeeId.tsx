import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { useState } from "react"
import { ProfilesService } from "@/client"
import { CapabilityList } from "@/components/Profile/CapabilityList"
import { EmployeeSummaryContent } from "@/components/Profile/EmployeeSummary"
import { EvidenceList } from "@/components/Profile/EvidenceList"
import { EvidenceSheet } from "@/components/Profile/EvidenceSheet"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { VersionHistory } from "@/components/Profile/VersionHistory"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/employees/$employeeId")({
  component: EmployeePage,
  head: () => ({ meta: [{ title: "员工画像 - 员工画像平台" }] }),
})

function EmployeePage() {
  const { org } = useOrg()
  const { employeeId } = Route.useParams()
  return <EmployeeDetails key={`${org?.id}:${employeeId}`} />
}

function EmployeeDetails() {
  const { org } = useOrg()
  const { employeeId } = Route.useParams()
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ["profile", org?.id, employeeId],
    queryFn: async () =>
      (
        await ProfilesService.readProfile({
          path: { org_id: org!.id, employee_id: employeeId },
        })
      ).data,
    enabled: !!org,
    retry: false,
  })
  if (!org) return <OrgState />
  if (query.isPending) return <ProfileLoading />
  if (query.isError) {
    if (!(isAxiosError(query.error) && query.error.response?.status === 404))
      return <ProfileError retry={() => query.refetch()} />
    return (
      <div role="alert">
        未找到该员工或你没有权限查看
        <Button variant="outline" onClick={() => query.refetch()}>
          重试
        </Button>
      </div>
    )
  }
  return (
    <div className="space-y-8">
      <EmployeeSummaryContent employee={query.data.employee} />
      <Tabs defaultValue="capabilities" className="gap-6">
        <TabsList aria-label="画像详情">
          <TabsTrigger value="capabilities">能力画像</TabsTrigger>
          <TabsTrigger value="evidence">证据</TabsTrigger>
          <TabsTrigger value="versions">版本历史</TabsTrigger>
        </TabsList>
        <TabsContent value="capabilities">
          <CapabilityList items={query.data.items} onEvidence={setEvidenceId} />
        </TabsContent>
        <TabsContent value="evidence">
          <EvidenceList
            orgId={org.id}
            employeeId={employeeId}
            onSelect={setEvidenceId}
          />
        </TabsContent>
        <TabsContent value="versions">
          <VersionHistory
            orgId={org.id}
            employeeId={employeeId}
            onEvidence={setEvidenceId}
          />
        </TabsContent>
      </Tabs>
      <EvidenceSheet
        orgId={org.id}
        evidenceId={evidenceId}
        onClose={() => setEvidenceId(null)}
      />
    </div>
  )
}
