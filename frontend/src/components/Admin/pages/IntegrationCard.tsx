import type { ReactNode } from "react"
import {
  SectionCard,
  SideDrawer,
} from "@/components/Common/WorkbenchPrimitives"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function IntegrationCard({
  name,
  icon,
  description,
  status,
  active,
  detail,
  suggested,
  action = "配置",
  disabled,
  children,
}: {
  name: string
  icon: ReactNode
  description: string
  status: string
  active?: boolean
  detail: ReactNode
  suggested?: boolean
  action?: string
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <SectionCard
      aria-label={name}
      className={cn(
        "flex flex-col gap-3 p-3.5",
        suggested && "border-primary/50 ring-1 ring-primary/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {icon}
        <h2 className="min-w-0 flex-1 text-sm font-semibold">{name}</h2>
        <Badge
          variant="secondary"
          className={cn(
            active &&
              "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
          )}
        >
          {status}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">{description}</p>
      <div className="mt-auto flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 border-t border-surface-border pt-2.5">
        <div className="min-w-0 break-words text-xs text-muted-foreground">
          {suggested && <p className="font-medium text-primary">有助手草稿</p>}
          {detail}
        </div>
        <SideDrawer
          title={`${action} · ${name}`}
          trigger={
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label={`${action} ${name}`}
              disabled={disabled}
            >
              {action}
            </Button>
          }
        >
          {children}
        </SideDrawer>
      </div>
    </SectionCard>
  )
}
