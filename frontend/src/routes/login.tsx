import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import type { Body_login_login_access_token as AccessToken } from "@/client"
import { SetupService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"

import { safeNext } from "@/utils"

const formSchema = z.object({
  username: z.string().trim().min(1, { message: "请输入邮箱" }),
  password: z
    .string()
    .min(1, { message: "请输入密码" })
    .min(8, { message: "密码至少需要 8 个字符" }),
}) satisfies z.ZodType<AccessToken>

type FormData = z.infer<typeof formSchema>

export const Route = createFileRoute("/login")({
  component: Login,
  validateSearch: (search: Record<string, unknown>): { next?: string } => ({
    next: search.next === undefined ? undefined : safeNext(search.next),
  }),
  beforeLoad: async ({ search }) => {
    if (isLoggedIn()) {
      throw redirect({
        href: safeNext(search.next),
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "登录 - 员工画像平台",
      },
    ],
  }),
})

function Login() {
  const [loggedOut, setLoggedOut] = useState(false)
  useEffect(() => {
    if (sessionStorage.getItem("logged_out")) {
      sessionStorage.removeItem("logged_out")
      setLoggedOut(true)
    }
  }, [])
  const status = useQuery({
    queryKey: ["setup-status"],
    queryFn: async () => (await SetupService.readStatus()).data,
  })
  const { next } = Route.useSearch()
  const { loginMutation } = useAuth(next)
  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      username: "",
      password: "",
    },
  })

  const onSubmit = (data: FormData) => {
    if (loginMutation.isPending) return
    loginMutation.mutate(data)
  }

  return (
    <AuthLayout>
      {loggedOut && (
        <p role="status" className="mb-6 rounded-lg border p-4 text-sm">
          已退出登录。在公用电脑上用完，请关闭浏览器。
        </p>
      )}
      {status.data?.initialized === false && (
        <p role="status" className="mb-6 rounded-lg border p-4 text-sm">
          系统还没有初始化。请使用部署时打印的初始化链接完成设置。
        </p>
      )}
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-2xl font-bold">登录</h1>
          </div>

          <div className="grid gap-4">
            <FormField
              control={form.control}
              name="username"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>邮箱</FormLabel>
                  <FormControl>
                    <Input
                      data-testid="email-input"
                      placeholder="请输入邮箱"
                      type="text"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage className="text-xs" />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center">
                    <FormLabel>密码</FormLabel>
                  </div>
                  <FormControl>
                    <PasswordInput
                      data-testid="password-input"
                      placeholder="请输入密码"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage className="text-xs" />
                </FormItem>
              )}
            />

            <LoadingButton type="submit" loading={loginMutation.isPending}>
              登录
            </LoadingButton>
          </div>

          <p className="text-center text-sm text-muted-foreground">
            忘记密码请联系企业管理员重置
          </p>
        </form>
      </Form>
    </AuthLayout>
  )
}
