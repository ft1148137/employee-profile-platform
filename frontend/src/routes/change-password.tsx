import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router"
import { KeyRound } from "lucide-react"
import { UsersService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import ChangePassword from "@/components/UserSettings/ChangePassword"
import { isLoggedIn } from "@/hooks/useAuth"

import { safeNext } from "@/utils"

export const Route = createFileRoute("/change-password")({
  validateSearch: (search: Record<string, unknown>): { next?: string } => ({
    next: search.next === undefined ? undefined : safeNext(search.next),
  }),
  beforeLoad: async ({ search }) => {
    if (!isLoggedIn())
      throw redirect({ to: "/login", search: { next: search.next } })
    const { data: user } = await UsersService.readUserMe()
    if (!user.must_change_password)
      throw redirect({ href: safeNext(search.next) })
  },
  component: ChangePasswordPage,
  head: () => ({ meta: [{ title: "设置新密码 - 员工画像平台" }] }),
})

function ChangePasswordPage() {
  const navigate = useNavigate()
  const { next } = Route.useSearch()
  return (
    <AuthLayout>
      <header className="mb-5 flex flex-col items-center gap-2 text-center">
        <span className="grid size-10 place-items-center rounded-lg bg-primary text-primary-foreground">
          <KeyRound className="size-5" aria-hidden="true" />
        </span>
        <h1 className="text-xl font-semibold">设置新密码</h1>
        <p className="text-sm text-muted-foreground">
          首次登录，请将初始密码修改为个人密码。
        </p>
      </header>
      <ChangePassword onSuccess={() => navigate({ href: safeNext(next) })} />
    </AuthLayout>
  )
}
