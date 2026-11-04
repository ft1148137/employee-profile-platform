import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  createFileRoute,
  useLocation,
  useNavigate,
} from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useLayoutEffect, useState } from "react"
import { SetupService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import { SetupError } from "@/components/Setup/SetupError"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"

const invalidLink =
  "初始化链接无效或已过期，请联系部署人员重新运行安装脚本获取新链接"

export const Route = createFileRoute("/setup")({
  component: Initialize,
  head: () => ({ meta: [{ title: "首次设置 - 员工画像平台" }] }),
})

function Initialize() {
  const hash = useLocation({ select: (location) => location.hash })
  const [token, setToken] = useState(() =>
    new URLSearchParams(window.location.hash.slice(1)).get("token"),
  )
  const navigate = useNavigate()
  const client = useQueryClient()
  const [confirmationError, setConfirmationError] = useState("")
  const mutation = useMutation({
    mutationFn: async (form: FormData) =>
      (
        await SetupService.initializeOrganization({
          body: {
            token: token!,
            org_name: String(form.get("org_name")).trim(),
            admin_username: String(form.get("admin_username")).trim(),
            admin_full_name: String(form.get("admin_full_name")).trim() || null,
            admin_password: String(form.get("admin_password")),
          },
        })
      ).data,
    onSuccess: (data) => {
      localStorage.setItem("access_token", data.access_token)
      localStorage.setItem("current_org_id", data.org_id)
      client.clear()
      navigate({ to: "/setup-wizard", replace: true })
    },
  })
  const { reset } = mutation
  useLayoutEffect(() => {
    if (!hash || !window.location.hash) return
    setToken(new URLSearchParams(hash.replace(/^#/, "")).get("token"))
    reset()
    setConfirmationError("")
    window.history.replaceState(
      window.history.state,
      "",
      window.location.pathname + window.location.search,
    )
  }, [hash, reset])
  const invalid =
    !token ||
    (mutation.error instanceof AxiosError &&
      mutation.error.response?.status === 400 &&
      mutation.error.response?.data?.detail ===
        "初始化链接无效或已过期，请联系部署人员重新获取")
  return (
    <AuthLayout>
      <h1 className="mb-2 text-2xl font-bold">欢迎设置你的企业</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        先建立企业和管理员账号，再逐步完成首次设置。
      </p>
      {invalid ? (
        <p role="alert">{invalidLink}</p>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (mutation.isPending) return
            const form = new FormData(event.currentTarget)
            if (form.get("admin_password") !== form.get("confirmation")) {
              setConfirmationError("两次输入的密码不一致")
              return
            }
            setConfirmationError("")
            mutation.mutate(form)
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="org_name">企业名称</Label>
            <Input id="org_name" name="org_name" required maxLength={100} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="admin_username">管理员邮箱</Label>
            <Input
              id="admin_username"
              name="admin_username"
              type="email"
              placeholder="admin@example.com"
              required
              maxLength={255}
              autoComplete="username"
              aria-describedby="admin-hint"
            />
            <p id="admin-hint" className="text-xs text-muted-foreground">
              邮箱就是登录名；管理员可以不在员工名单中。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="admin_full_name">姓名（可选）</Label>
            <Input
              id="admin_full_name"
              name="admin_full_name"
              maxLength={255}
              autoComplete="name"
            />
          </div>
          {(
            [
              ["admin_password", "密码"],
              ["confirmation", "确认密码"],
            ] as const
          ).map(([name, label]) => (
            <div key={name} className="space-y-2">
              <Label htmlFor={name}>{label}</Label>
              <PasswordInput
                id={name}
                name={name}
                required
                minLength={8}
                maxLength={128}
                autoComplete="new-password"
              />
            </div>
          ))}
          {confirmationError && (
            <p role="alert" className="text-sm text-destructive">
              {confirmationError}
            </p>
          )}
          <SetupError error={mutation.error} />
          <LoadingButton
            type="submit"
            loading={mutation.isPending}
            className="w-full"
          >
            创建企业并继续
          </LoadingButton>
        </form>
      )}
    </AuthLayout>
  )
}
