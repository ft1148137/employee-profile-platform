import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router"
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
  head: () => ({ meta: [{ title: "请设置新密码 - 员工画像平台" }] }),
})

function ChangePasswordPage() {
  const navigate = useNavigate()
  const { next } = Route.useSearch()
  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold">请设置新密码</h1>
      <p className="my-4 text-sm text-muted-foreground">
        这是你第一次登录，请把管理员给你的初始密码改成只有你自己知道的密码
      </p>
      <ChangePassword onSuccess={() => navigate({ href: safeNext(next) })} />
    </AuthLayout>
  )
}
