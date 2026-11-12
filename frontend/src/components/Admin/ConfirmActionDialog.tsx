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
  open: controlledOpen,
  onOpenChange,
}: {
  title: string
  description: string
  action: () => Promise<unknown>
  onSuccess: () => void
  open?: boolean
  onOpenChange?: (open: boolean) => void
  disabled?: boolean
}) {
  const [internalOpen, setOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const mutation = useMutation({
    mutationFn: action,
    onSuccess: () => {
      setOpen(false)
      onOpenChange?.(false)
      onSuccess()
    },
  })
  function changeOpen(value: boolean) {
    if (mutation.isPending) return
    setOpen(value)
    onOpenChange?.(value)
    mutation.reset()
  }
  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      {controlledOpen === undefined && (
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => changeOpen(true)}
        >
          {title}
        </Button>
      )}
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
