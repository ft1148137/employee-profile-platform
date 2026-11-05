import { createFileRoute } from "@tanstack/react-router"
import { ApprovalPolicy } from "@/components/Admin/ApprovalPolicy"
import { Appearance } from "@/components/Common/Appearance"
import { CapabilityDictionary } from "@/components/Setup/CapabilityDictionary"
import { AiConnections } from "@/components/UserSettings/AiConnections"
import ChangePassword from "@/components/UserSettings/ChangePassword"
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
  { value: "dictionary", title: "能力词典", component: CapabilityDictionary },
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
  const { org, roles } = useOrg()
  const admin = roles.includes("org_admin")

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
      </div>

      <Tabs defaultValue="my-profile">
        <TabsList className="h-auto flex-wrap justify-start">
          {tabsConfig.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.title}
            </TabsTrigger>
          ))}
          {admin && <TabsTrigger value="approval-policy">审批规则</TabsTrigger>}
        </TabsList>
        {tabsConfig.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <tab.component />
          </TabsContent>
        ))}
        {admin && org && (
          <TabsContent value="approval-policy">
            <ApprovalPolicy key={org.id} orgId={org.id} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
