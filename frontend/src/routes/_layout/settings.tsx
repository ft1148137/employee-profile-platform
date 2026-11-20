import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { Appearance } from "@/components/Common/Appearance"
import {
  SectionCard,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
import { AutoDraftSetting } from "@/components/Profile/AutoDraftSetting"
import { AiConnections } from "@/components/UserSettings/AiConnections"
import ChangePassword from "@/components/UserSettings/ChangePassword"
import { ExternalIdentities } from "@/components/UserSettings/ExternalIdentities"
import { ImBindings } from "@/components/UserSettings/ImBindings"
import { ReviewDelegation } from "@/components/UserSettings/ReviewDelegation"
import UserInformation from "@/components/UserSettings/UserInformation"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
    <WorkspacePage variant="form" className="space-y-4" key={org?.id}>
      {section === "account" && (
        <>
          <h1 className="sr-only">账号与安全</h1>
          <UserInformation />
          <PasswordSetting />
          <SectionCard
            aria-label="外观"
            className="flex items-center justify-between gap-3"
          >
            <div>
              <h2 className="text-sm font-semibold">外观</h2>
              <p className="text-sm text-muted-foreground">
                选择浅色、深色或跟随系统。
              </p>
            </div>
            <Appearance />
          </SectionCard>
        </>
      )}
      {section === "integrations" && (
        <>
          <h1 className="sr-only">连接与集成</h1>
          <SectionCard className="divide-y overflow-hidden p-0">
            <h2 className="bg-muted/40 px-4 py-2.5 text-sm font-semibold">
              聊天账号与外部工作身份
            </h2>
            <ImBindings />
            {org && employeeId && <ExternalIdentities orgId={org.id} />}
          </SectionCard>
          <AiConnections />
        </>
      )}
      {section === "preferences" && (
        <>
          <h1 className="sr-only">偏好与代理</h1>
          {org && employeeId && <AutoDraftSetting orgId={org.id} />}
          <ReviewDelegation />
        </>
      )}
    </WorkspacePage>
  )
}

function PasswordSetting() {
  const [open, setOpen] = useState(false)
  return (
    <SectionCard className="flex items-center justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold">登录密码</h2>
        <p className="text-sm text-muted-foreground">使用当前密码验证身份。</p>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" size="sm">
            修改密码
          </Button>
        </DialogTrigger>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>修改密码</DialogTitle>
            <DialogDescription>输入当前密码与新密码。</DialogDescription>
          </DialogHeader>
          <ChangePassword onSuccess={() => setOpen(false)} />
        </DialogContent>
      </Dialog>
    </SectionCard>
  )
}
