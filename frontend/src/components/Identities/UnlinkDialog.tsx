import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog"

export function UnlinkDialog({
  account,
  pending,
  error,
  onConfirm,
  onClose,
}: {
  account: string
  pending: boolean
  error: Error | null
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent showCloseButton={!pending}>
        <DialogTitle>解除关联</DialogTitle>
        <DialogDescription>
          解除后，账号「{account}
          」产生的记录将从该员工画像中移除（可随时重新关联）。确认解除吗？
        </DialogDescription>
        <SetupError error={error} />
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button variant="destructive" disabled={pending} onClick={onConfirm}>
            {pending ? "正在解除…" : "确认解除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
