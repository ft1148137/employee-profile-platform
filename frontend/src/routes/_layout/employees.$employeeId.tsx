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
import { WorkspacePage } from "@/components/Common/WorkbenchPrimitives"
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
    if (detailTab !== "capabilities" || !requestId || !requests.data) return
    const request = document.getElementById(`request-${requestId}`)
    const disclosure = request?.closest("details")
    if (disclosure) disclosure.open = true
    request?.scrollIntoView({ block: "center" })
  }, [detailTab, requestId, requests.data])
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
  const active = query.data.employee.employment_status === "active"
  const canEditOwn = active && ownEmployeeId === employeeId
  const canDirectEdit =
    ownEmployeeId !== employeeId &&
    active &&
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
    <WorkspacePage>
      <EmployeeDetailTabs
        employee={query.data.employee}
        tab={detailTab}
        onTabChange={(tab) => navigate({ search: { tab } })}
        evidence={
          <>
            <section aria-label="证据" className="space-y-4">
              <h2 className="text-lg font-semibold">证据</h2>
              <EvidenceList
                orgId={org.id}
                employeeId={employeeId}
                onSelect={setEvidenceId}
              />
            </section>
            <section aria-label="版本历史" className="space-y-4 border-t pt-6">
              <h2 className="text-lg font-semibold">版本历史</h2>
              <VersionHistory
                orgId={org.id}
                employeeId={employeeId}
                onEvidence={setEvidenceId}
                onRequest={(id) => {
                  setRequestId(id)
                  navigate({ search: { tab: "capabilities" } })
                }}
              />
            </section>
          </>
        }
      >
        {isAdmin && canEditOwn && <AutoDraftSetting orgId={org.id} />}
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
            isOwner={canEditOwn}
            onEvidence={setEvidenceId}
            orgId={org.id}
            onEdit={setEditing}
          />
        )}
        {!query.data.latest_published &&
          !query.data.items.some((item) => item.claim) && (
            <section
              aria-label="画像说明"
              className="space-y-1.5 rounded-lg border border-surface-border bg-surface p-3 text-sm"
            >
              <h2 className="font-semibold">
                {ownEmployeeId === employeeId
                  ? "画像暂为空"
                  : "该员工暂未建立画像"}
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
              {canEditOwn ? (
                <p>
                  可点击顶栏「添加能力」手动补充，或前往
                  <Link to="/assistant" className="text-primary underline">
                    「AI 助手」
                  </Link>
                  口述经历生成。
                </p>
              ) : (
                active && (
                  <p>
                    该员工可以通过「添加能力」自己补充，或去助手里描述经历。
                  </p>
                )
              )}
              <p>画像仅用于跨部门协作找人，不用于绩效、薪酬或晋升。</p>
            </section>
          )}
        {!query.data.schema_version && (
          <p role="status" className="mb-6 text-muted-foreground">
            企业尚未配置能力词典，请联系管理员。
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
          appeals={
            canEditOwn && requests.data
              ? { orgId: org.id, requests: requests.data.data }
              : undefined
          }
          onEdit={
            canEditOwn
              ? (item, retract) =>
                  retract ? setEditing({ item, retract }) : setChoosing(item)
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
      </EmployeeDetailTabs>
      {evidenceId && (
        <EvidenceSheet
          orgId={org.id}
          evidenceId={evidenceId}
          onClose={() => setEvidenceId(null)}
        />
      )}
      {editing && canEditOwn && (
        <ChangeRequestEditor
          orgId={org.id}
          selection={editing}
          profileItems={query.data.items}
          onClose={() => setEditing(null)}
        />
      )}
      {choosing && canEditOwn && (
        <Dialog open onOpenChange={(open) => !open && setChoosing(null)}>
          <DialogContent>
            <DialogTitle>编辑「{choosing.capability.name}」</DialogTitle>
            <DialogDescription>
              可手动填写，或直接告诉 AI 助手帮你整理。
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
                用助手整理
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
    </WorkspacePage>
  )
}
