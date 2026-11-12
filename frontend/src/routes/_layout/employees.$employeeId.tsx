import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { useEffect, useState } from "react"
import { ChangeRequestsService, ProfilesService } from "@/client"
import { tabSearch } from "@/components/Admin/AdminTabs"
import { AutoDraftSetting } from "@/components/Profile/AutoDraftSetting"
import { CapabilityList } from "@/components/Profile/CapabilityList"
import { ChangeRequestCard } from "@/components/Profile/ChangeRequestCard"
import {
  ChangeRequestEditor,
  type EditorSelection,
} from "@/components/Profile/ChangeRequestEditor"
import { EmployeeDetailTabs } from "@/components/Profile/EmployeeDetailTabs"
import { EvidenceList } from "@/components/Profile/EvidenceList"
import { EvidenceSheet } from "@/components/Profile/EvidenceSheet"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileImport } from "@/components/Profile/ProfileImport"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { VersionHistory } from "@/components/Profile/VersionHistory"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/employees/$employeeId")({
  component: EmployeePage,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "员工详情 - 员工画像平台" }] }),
})

function EmployeePage() {
  const { org } = useOrg()
  const { employeeId } = Route.useParams()
  return <EmployeeDetails key={`${org?.id}:${employeeId}`} />
}

function EmployeeDetails() {
  const { org, roles, employeeId: ownEmployeeId } = useOrg()
  const { employeeId } = Route.useParams()
  const { tab: detailTab } = Route.useSearch()
  const navigate = Route.useNavigate()
  const [tab, setTab] = useState("capabilities")
  const [requestId, setRequestId] = useState<string | null>(null)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [editing, setEditing] = useState<EditorSelection | null>(null)
  const requests = useQuery({
    queryKey: ["change-requests", org?.id, employeeId],
    queryFn: async () =>
      (
        await ChangeRequestsService.readChangeRequests({
          path: { org_id: org!.id, employee_id: employeeId },
          query: { status: "all" },
        })
      ).data,
    enabled: !!org,
    retry: false,
  })
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
  useEffect(() => {
    if (tab === "capabilities" && requestId && requests.data)
      document
        .getElementById(`request-${requestId}`)
        ?.scrollIntoView({ block: "center" })
  }, [tab, requestId, requests.data])
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
      <EmployeeDetailTabs
        employee={query.data.employee}
        tab={detailTab}
        onTabChange={(tab) => navigate({ search: { tab } })}
      >
        {ownEmployeeId === employeeId && <AutoDraftSetting orgId={org.id} />}
        {ownEmployeeId === employeeId && <ProfileImport orgId={org.id} />}
        <Tabs value={tab} onValueChange={setTab} className="gap-6">
          <TabsList aria-label="画像详情">
            <TabsTrigger value="capabilities">能力画像</TabsTrigger>
            <TabsTrigger value="evidence">证据</TabsTrigger>
            <TabsTrigger value="versions">版本历史</TabsTrigger>
          </TabsList>
          <TabsContent value="capabilities">
            {requests.isPending ? (
              <ProfileLoading />
            ) : requests.isError ? (
              <ProfileError retry={() => requests.refetch()} />
            ) : (
              <ChangeRequestCard
                requests={requests.data.data}
                employeeId={employeeId}
                isOwner={ownEmployeeId === employeeId}
                onEvidence={setEvidenceId}
                orgId={org.id}
                onEdit={setEditing}
              />
            )}
            {!query.data.latest_published &&
              !query.data.items.some((item) => item.claim) && (
                <section
                  aria-label="画像说明"
                  className="mb-6 rounded-xl border bg-muted/20 p-6 space-y-3"
                >
                  <h2 className="font-semibold">
                    {ownEmployeeId === employeeId
                      ? "你的画像还没有内容"
                      : "该员工的画像还没有内容"}
                  </h2>
                  <p>
                    {ownEmployeeId === employeeId
                      ? "企业接入工作记录后，系统会先起草一份画像给你本人过目，你确认或修改后才会提交审核。"
                      : "企业接入工作记录后，系统会先起草一份画像给该员工本人过目，由本人确认或修改后才会提交审核。"}
                  </p>
                  <p>
                    画像只用来帮助跨部门找到合适的人，不用于绩效、薪酬和晋升。
                  </p>
                </section>
              )}
            {!query.data.schema_version && (
              <p role="status" className="mb-6 text-muted-foreground">
                企业管理员还没有选择能力词典
              </p>
            )}
            <CapabilityList
              items={query.data.items}
              departmentConfigured={
                query.data.department_capabilities_configured ?? false
              }
              canConfigureDepartment={roles.includes("org_admin")}
              onEvidence={setEvidenceId}
              onEdit={
                ownEmployeeId === employeeId
                  ? (item, retract) => setEditing({ item, retract })
                  : undefined
              }
              pendingCapabilityIds={
                new Set(
                  requests.data?.data
                    .filter((request) =>
                      ["draft", "pending_owner", "pending_review"].includes(
                        request.status,
                      ),
                    )
                    .flatMap((request) =>
                      (request.items ?? []).map((item) => item.capability.id),
                    ),
                )
              }
            />
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
              onRequest={(id) => {
                setRequestId(id)
                setTab("capabilities")
              }}
            />
          </TabsContent>
        </Tabs>
      </EmployeeDetailTabs>
      <EvidenceSheet
        orgId={org.id}
        evidenceId={evidenceId}
        onClose={() => setEvidenceId(null)}
      />
      {editing && ownEmployeeId === employeeId && (
        <ChangeRequestEditor
          orgId={org.id}
          selection={editing}
          profileItems={query.data.items}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}
