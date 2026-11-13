import type { ProfileItem } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { levelLabel } from "./fields"
import { StatusBadge } from "./StatusBadge"

export function CapabilityDetailDialog({
  item,
  directEdit,
  onEdit,
  onClose,
}: {
  item: ProfileItem
  directEdit: boolean
  onEdit?: (item: ProfileItem) => void
  onClose: () => void
}) {
  const { capability, claim, status } = item
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogTitle>{capability.name}</DialogTitle>
        <DialogDescription>能力详情（只读）</DialogDescription>
        <p>
          等级：
          {claim?.level
            ? levelLabel(capability.level_rubric, claim.level)
            : "未定级"}
        </p>
        <p>
          状态：
          <StatusBadge kind="assessment_status" value={status} />
        </p>
        <p className="whitespace-pre-wrap break-words">
          {claim?.statement ?? "暂无判断，未知不代表不具备这项能力"}
        </p>
        <p className="text-sm text-muted-foreground">
          最近更新时间：
          {claim?.created_at
            ? new Date(claim.created_at).toLocaleString("zh-CN", {
                timeZone: "Asia/Shanghai",
              })
            : "—"}
        </p>
        {onEdit && !item.retired && (
          <Button
            onClick={() => {
              onClose()
              onEdit(item)
            }}
          >
            {directEdit ? "直接修改" : "修改"}
          </Button>
        )}
      </DialogContent>
    </Dialog>
  )
}
