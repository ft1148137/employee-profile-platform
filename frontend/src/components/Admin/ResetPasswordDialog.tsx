import { useMutation } from "@tanstack/react-query"
import { useId, useRef, useState } from "react"
import { AccountsService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"

export function ResetPasswordDialog({
  orgId,
  userId,
  name,
  open,
  onClose,
}: {
  orgId: string
  userId: string
  name: string
  open: boolean
  onClose: () => void
}) {
  const [copyMessage, setCopyMessage] = useState("")
  const passwordId = useId()
  const password = useRef<HTMLInputElement>(null)
  const mutation = useMutation({
    mutationFn: async () =>
      (
        await AccountsService.resetPassword({
          path: { org_id: orgId, user_id: userId },
        })
      ).data,
    gcTime: 0,
  })
  function changeOpen(value: boolean) {
    if (mutation.isPending) return
    if (!value) {
      mutation.reset()
      setCopyMessage("")
      onClose()
    }
  }
  async function copy() {
    if (!mutation.data) return
    try {
      await navigator.clipboard.writeText(mutation.data.initial_password)
      setCopyMessage("已复制")
      return
    } catch {
      /* HTTP deployments use the legacy fallback. */
    }
    password.current?.select()
    try {
      if (document.execCommand("copy")) {
        setCopyMessage("已复制")
        return
      }
    } catch {
      /* Keep the password selected for manual copying. */
    }
    setCopyMessage("请手动复制")
  }
  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>重置密码 · {name}</DialogTitle>
          <DialogDescription>
            {mutation.data
              ? "新密码只显示这一次，请单独交给员工。关闭后不能再查看。"
              : "重置后原密码立即失效，员工已经打开的页面需要重新登录；员工用新密码登录后必须修改密码。"}
          </DialogDescription>
        </DialogHeader>
        {mutation.data ? (
          <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
            <p>登录名：{mutation.data.username}</p>
            <p>姓名：{mutation.data.display_name ?? name}</p>
            <label className="block" htmlFor={passwordId}>
              新密码
              <Input
                id={passwordId}
                ref={password}
                aria-label="新密码"
                readOnly
                value={mutation.data.initial_password}
              />
            </label>
            <Button variant="outline" onClick={copy}>
              复制
            </Button>
            <p role="status">{copyMessage}</p>
          </div>
        ) : (
          <>
            <SetupError error={mutation.error} />
            <Button
              disabled={mutation.isPending}
              onClick={() => {
                mutation.mutate()
              }}
            >
              {mutation.isPending ? "正在重置…" : "确认重置密码"}
            </Button>
          </>
        )}
        <Button
          variant="outline"
          disabled={mutation.isPending}
          onClick={() => changeOpen(false)}
        >
          {mutation.data ? "关闭" : "取消"}
        </Button>
      </DialogContent>
    </Dialog>
  )
}
