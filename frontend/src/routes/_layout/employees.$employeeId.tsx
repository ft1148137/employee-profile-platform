import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { useEffect, useState } from "react"
import {
  ChangeRequestsService,
  type ProfileItem,
  ProfilesService,
} from "@/client"
import { tabSearch } from "@/components/Admin/AdminTabs"
import { AutoDraftSetting } from "@/components/Profile/AutoDraftSetting"
import { CapabilityList } from "@/components/Profile/CapabilityList"
import { ChangeRequestCard } from "@/components/Profile/ChangeRequestCard"
import {
  ChangeRequestEditor,
  type EditorSelection,
} from "@/components/Profile/ChangeRequestEditor"
import { DirectEditDialog } from "@/components/Profile/DirectEditDialog"
import { EmployeeDetailTabs } from "@/components/Profile/EmployeeDetailTabs"
import { EvidenceList } from "@/components/Profile/EvidenceList"
import { EvidenceSheet } from "@/components/Profile/EvidenceSheet"
import { levelLabel } from "@/components/Profile/fields"
import { initialStatusReason } from "@/components/Profile/initialStatus"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { VersionHistory } from "@/components/Profile/VersionHistory"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
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
  const {
    org,
    roles,
    employeeId: ownEmployeeId,
    managedDepartmentIds,
  } = useOrg()
  const isAdmin = roles.includes("org_admin")
  const { employeeId } = Route.useParams()
  const { tab: detailTab } = Route.useSearch()
  const navigate = Route.useNavigate()
  const [tab, setTab] = useState("capabilities")
  const [requestId, setRequestId] = useState<string | null>(null)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [editing, setEditing] = useState<EditorSelection | null>(null)
  const [choosing, setChoosing] = useState<ProfileItem | null>(null)
  const [directEditing, setDirectEditing] = useState<ProfileItem | null>(null)
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
  const canDirectEdit =
    ownEmployeeId !== employeeId &&
    query.data.employee.employment_status === "active" &&
    (isAdmin ||
      managedDepartmentIds.includes(query.data.employee.department?.id ?? ""))
  const pendingSupplement = requests.data?.data.find(
    (request) =>
      request.status === "pending_review" && request.origin !== "initial_sync",
  )
  const reviewerNames = (pendingSupplement?.reviewers ?? [])
    .map((reviewer) => `${reviewer.name}（${reviewer.role}）`)
    .join("、")
  return (
    <div className="space-y-8">
      <EmployeeDetailTabs
        employee={query.data.employee}
        tab={detailTab}
        onTabChange={(tab) => navigate({ search: { tab } })}
      >
        {isAdmin && ownEmployeeId === employeeId && (
          <AutoDraftSetting orgId={org.id} />
        )}
        <Tabs
          value={isAdmin ? tab : "capabilities"}
          onValueChange={setTab}
          className="gap-6"
        >
          {isAdmin && (
            <TabsList aria-label="画像详情">
              <TabsTrigger value="capabilities">能力画像</TabsTrigger>
              <TabsTrigger value="evidence">证据</TabsTrigger>
              <TabsTrigger value="versions">版本历史</TabsTrigger>
            </TabsList>
          )}
          <TabsContent value="capabilities">
            {requests.isPending ? (
              <ProfileLoading />
            ) : requests.isError ? (
              <ProfileError retry={() => requests.refetch()} />
            ) : (
              <ChangeRequestCard
                requests={requests.data.data}
                compact={!isAdmin}
                profileItems={query.data.items}
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
                    {initialStatusReason(
                      query.data.employee.progress?.initial_profile_status,
                      ownEmployeeId === employeeId,
                    )}
                  </p>
                  {ownEmployeeId === employeeId && pendingSupplement && (
                    <p>
                      {reviewerNames
                        ? `你的补充正在等 ${reviewerNames} 审批。`
                        : "你的补充正在等待审批，暂时没有可用的审批人，企业管理员会处理。"}
                    </p>
                  )}
                  {ownEmployeeId === employeeId ? (
                    <p>
                      你可以在下面的「添加能力」里自己补充，或者
                      <Link to="/assistant" className="text-primary underline">
                        去助手
                      </Link>
                      里描述你的经历。
                    </p>
                  ) : (
                    <p>
                      该员工可以通过「添加能力」自己补充，或去助手里描述经历。
                    </p>
                  )}
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
              compact={!isAdmin}
              departmentConfigured={
                query.data.department_capabilities_configured ?? false
              }
              canConfigureDepartment={roles.includes("org_admin")}
              onEvidence={setEvidenceId}
              directEdit={canDirectEdit}
              onEdit={
                ownEmployeeId === employeeId
                  ? (item, retract) =>
                      retract
                        ? setEditing({ item, retract })
                        : setChoosing(item)
                  : canDirectEdit
                    ? setDirectEditing
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
          {isAdmin && (
            <TabsContent value="evidence">
              <EvidenceList
                orgId={org.id}
                employeeId={employeeId}
                onSelect={setEvidenceId}
              />
            </TabsContent>
          )}
          {isAdmin && (
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
          )}
        </Tabs>
      </EmployeeDetailTabs>
      {isAdmin && (
        <EvidenceSheet
          orgId={org.id}
          evidenceId={evidenceId}
          onClose={() => setEvidenceId(null)}
        />
      )}
      {editing && ownEmployeeId === employeeId && (
        <ChangeRequestEditor
          orgId={org.id}
          selection={editing}
          profileItems={query.data.items}
          onClose={() => setEditing(null)}
        />
      )}
      {choosing && (
        <Dialog open onOpenChange={(open) => !open && setChoosing(null)}>
          <DialogContent>
            <DialogTitle>编辑「{choosing.capability.name}」</DialogTitle>
            <DialogDescription>
              选择手动编辑，或在助手中描述你想做的修改。
            </DialogDescription>
            <Button
              onClick={() => {
                setEditing({ item: choosing })
                setChoosing(null)
              }}
            >
              手动编辑
            </Button>
            <Button variant="outline" asChild>
              <Link
                to="/assistant"
                search={{
                  prompt: `我想修改「${choosing.capability.name}」这项能力，现在的等级是${choosing.claim?.level ? levelLabel(choosing.capability.level_rubric, choosing.claim.level) : "未填写"}：`,
                }}
              >
                让助手改
              </Link>
            </Button>
          </DialogContent>
        </Dialog>
      )}
      {directEditing && canDirectEdit && (
        <DirectEditDialog
          orgId={org.id}
          employeeId={employeeId}
          item={directEditing}
          revision={query.data.latest_published?.revision ?? null}
          onClose={() => setDirectEditing(null)}
        />
      )}
    </div>
  )
}
