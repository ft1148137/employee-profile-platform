import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

export function ConfirmActionDialog({
  title,
  description,
  action,
  onSuccess,
  disabled = false,
}: {
  title: string
  description: string
  action: () => Promise<unknown>
  onSuccess: () => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: action,
    onSuccess: () => {
      setOpen(false)
      onSuccess()
    },
  })
  function changeOpen(value: boolean) {
    if (mutation.isPending) return
    setOpen(value)
    mutation.reset()
  }
  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => changeOpen(true)}
      >
        {title}
      </Button>
      <DialogContent showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <SetupError error={mutation.error} />
        <Button
          disabled={disabled || mutation.isPending}
          onClick={() => {
            mutation.mutate()
          }}
        >
          {mutation.isPending ? "正在处理…" : `确认${title}`}
        </Button>
        <Button
          variant="outline"
          disabled={mutation.isPending}
          onClick={() => changeOpen(false)}
        >
          取消
        </Button>
      </DialogContent>
    </Dialog>
  )
}
