import { createFileRoute } from "@tanstack/react-router"
import { Appearance } from "@/components/Common/Appearance"
import ChangePassword from "@/components/UserSettings/ChangePassword"
import UserInformation from "@/components/UserSettings/UserInformation"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"

const tabsConfig = [
  { value: "my-profile", title: "个人信息", component: UserInformation },
  { value: "password", title: "修改密码", component: ChangePassword },
  { value: "appearance", title: "外观", component: Appearance },
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
        <TabsList>
          {tabsConfig.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabsConfig.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <tab.component />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
