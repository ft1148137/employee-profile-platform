import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { EmployeeRequestsService } from "@/client"
import { Appearance } from "@/components/Common/Appearance"
import { AutoDraftSetting } from "@/components/Profile/AutoDraftSetting"
import { EmployeeFields } from "@/components/Profile/EmployeeDetailTabs"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { AiConnections } from "@/components/UserSettings/AiConnections"
import ChangePassword from "@/components/UserSettings/ChangePassword"
import { ExternalIdentities } from "@/components/UserSettings/ExternalIdentities"
import { ImBindings } from "@/components/UserSettings/ImBindings"
import { ReviewDelegation } from "@/components/UserSettings/ReviewDelegation"
import UserInformation from "@/components/UserSettings/UserInformation"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

const tabsConfig = [
  { value: "my-profile", title: "个人信息", component: UserInformation },
  { value: "password", title: "修改密码", component: ChangePassword },
  { value: "appearance", title: "外观", component: Appearance },
  { value: "assistants", title: "连接 AI 助手", component: AiConnections },
  { value: "im", title: "IM 绑定", component: ImBindings },
  {
    value: "review-delegation",
    title: "代理审批",
    component: ReviewDelegation,
  },
]

export const Route = createFileRoute("/_layout/settings")({
  component: UserSettings,
  head: () => ({
    meta: [
      {
        title: "设置 - 员工画像平台",
      },
    ],
  }),
})

function UserSettings() {
  const { user: currentUser } = useAuth()
  const { org, employeeId } = useOrg()

  if (!currentUser) {
    return null
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">设置</h1>
        <p className="text-muted-foreground">
          查看个人信息、修改密码和调整外观
        </p>
        <a
          className="text-sm text-primary underline"
          href="/admin/rules?tab=dictionary"
        >
          查看企业能力词典
        </a>
      </div>

      <Tabs defaultValue="my-profile">
        <TabsList className="h-auto flex-wrap justify-start">
          {org &&
            employeeId &&
            [
              ["fields", "档案"],
              ["identities", "Jira 账号"],
              ["auto-draft", "自动起草"],
            ].map(([value, title]) => (
              <TabsTrigger key={title} value={value}>
                {title}
              </TabsTrigger>
            ))}
          {tabsConfig.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {org && employeeId && (
          <>
            <TabsContent value="fields">
              <MyEmployeeFields key={org.id} orgId={org.id} />
            </TabsContent>
            <TabsContent value="identities">
              <ExternalIdentities key={org.id} orgId={org.id} />
            </TabsContent>
            <TabsContent value="auto-draft">
              <AutoDraftSetting key={org.id} orgId={org.id} />
            </TabsContent>
          </>
        )}
        {tabsConfig.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <tab.component />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function MyEmployeeFields({ orgId }: { orgId: string }) {
  const profile = useQuery({
    queryKey: ["my-profile", orgId],
    queryFn: async () =>
      (await EmployeeRequestsService.readMyProfile({ path: { org_id: orgId } }))
        .data,
  })
  if (profile.isPending) return <ProfileLoading />
  if (profile.isError) return <ProfileError retry={() => profile.refetch()} />
  return <EmployeeFields summary={profile.data.employee} />
}
