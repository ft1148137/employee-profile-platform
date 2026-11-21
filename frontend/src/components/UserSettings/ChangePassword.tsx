import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { type UpdatePassword, UsersService } from "@/client"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const password = z
  .string()
  .min(1, { message: "请输入密码" })
  .refine((value) => [...value].length >= 8, "密码至少需要 8 个字符")
  .refine((value) => [...value].length <= 128, "密码最多 128 个字符")

const formSchema = (email?: string) =>
  z
    .object({
      current_password: password,
      new_password: password,
      confirm_password: z.string().min(1, { message: "请再次输入新密码" }),
    })
    .refine(
      (data) =>
        !data.new_password || data.new_password !== data.current_password,
      {
        message: "新密码不能与当前密码相同",
        path: ["new_password"],
      },
    )
    .refine(
      (data) => data.new_password.trim().toLowerCase() !== email?.toLowerCase(),
      {
        message: "新密码不能与登录邮箱相同",
        path: ["new_password"],
      },
    )
    .refine((data) => data.new_password === data.confirm_password, {
      message: "两次输入的密码不一致",
      path: ["confirm_password"],
    })

type FormData = z.infer<ReturnType<typeof formSchema>>

const ChangePassword = ({ onSuccess }: { onSuccess?: () => void }) => {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const form = useForm<FormData>({
    resolver: zodResolver(formSchema(user?.username)),
    mode: "onChange",
    criteriaMode: "all",
    defaultValues: {
      current_password: "",
      new_password: "",
      confirm_password: "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: UpdatePassword) =>
      UsersService.updatePasswordMe({ body: data }),
    onSuccess: async ({ data }) => {
      localStorage.setItem("access_token", data.access_token)
      await queryClient.invalidateQueries({ queryKey: ["currentUser"] })
      showSuccessToast("密码修改成功")
      form.reset()
      onSuccess?.()
    },
    onError: handleError.bind(showErrorToast),
  })

  const onSubmit = async (data: FormData) => {
    mutation.mutate(data)
  }

  return (
    <div>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-4"
        >
          <p className="text-sm text-muted-foreground">
            新密码须为 8–128 个字符，不能与当前密码或登录邮箱相同。
          </p>
          <FormField
            control={form.control}
            name="current_password"
            rules={{ deps: "new_password" }}
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>当前密码</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="current-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="new_password"
            rules={{ deps: "confirm_password" }}
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>新密码</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="new-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="confirm_password"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>确认新密码</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="confirm-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <LoadingButton type="submit" loading={mutation.isPending}>
            保存密码
          </LoadingButton>
        </form>
      </Form>
    </div>
  )
}

export default ChangePassword
