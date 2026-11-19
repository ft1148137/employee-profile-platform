import { createFileRoute } from "@tanstack/react-router"
import { Appearance } from "@/components/Common/Appearance"
import { AutoDraftSetting } from "@/components/Profile/AutoDraftSetting"
import { AiConnections } from "@/components/UserSettings/AiConnections"
import ChangePassword from "@/components/UserSettings/ChangePassword"
import { ExternalIdentities } from "@/components/UserSettings/ExternalIdentities"
import { ImBindings } from "@/components/UserSettings/ImBindings"
import { ReviewDelegation } from "@/components/UserSettings/ReviewDelegation"
import UserInformation from "@/components/UserSettings/UserInformation"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/settings")({
  component: UserSettings,
  validateSearch: (
    search: Record<string, unknown>,
  ): {
    section?: "account" | "integrations" | "preferences"
  } => ({
    section:
      search.section === "account" ||
      search.section === "integrations" ||
      search.section === "preferences"
        ? search.section
        : undefined,
  }),
  head: () => ({ meta: [{ title: "设置 - 员工画像平台" }] }),
})

function UserSettings() {
  const { user: currentUser } = useAuth()
  const { org, employeeId } = useOrg()
  const { section = "account" } = Route.useSearch()

  if (!currentUser) return null

  return (
    <div className="max-w-3xl space-y-6" key={org?.id}>
      {section === "account" && (
        <>
          <h1 className="text-3xl font-semibold">账号与安全</h1>
          <section aria-label="个人信息" className="rounded-xl border px-5">
            <UserInformation />
          </section>
          <div className="rounded-xl border px-5 pb-5">
            <ChangePassword />
          </div>
          <section
            aria-label="外观"
            className="flex items-center justify-between rounded-xl border p-5"
          >
            <h2 className="text-lg font-semibold">外观</h2>
            <Appearance />
          </section>
        </>
      )}
      {section === "integrations" && (
        <>
          <h1 className="text-3xl font-semibold">连接与集成</h1>
          <div className="rounded-xl border px-5">
            <ImBindings />
          </div>
          <div className="rounded-xl border p-5">
            <AiConnections />
          </div>
          {org && employeeId && (
            <div className="rounded-xl border px-5">
              <ExternalIdentities orgId={org.id} />
            </div>
          )}
        </>
      )}
      {section === "preferences" && (
        <>
          <h1 className="text-3xl font-semibold">偏好与代理</h1>
          {org && employeeId && <AutoDraftSetting orgId={org.id} />}
          <div className="rounded-xl border px-5">
            <ReviewDelegation />
          </div>
        </>
      )}
    </div>
  )
}
