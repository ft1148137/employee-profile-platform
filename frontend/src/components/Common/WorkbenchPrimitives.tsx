import {
  type ComponentProps,
  type ReactElement,
  type ReactNode,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const pageStyles = {
  full: "p-3 md:p-4",
  split: "min-h-[calc(100dvh-3rem)]",
  form: "mx-auto max-w-4xl p-3 md:p-4",
}

export function WorkspacePage({
  variant = "full",
  className,
  ...props
}: ComponentProps<"div"> & { variant?: keyof typeof pageStyles }) {
  return (
    <div
      data-workspace-page=""
      className={cn("w-full min-w-0", pageStyles[variant], className)}
      {...props}
    />
  )
}

export function SectionCard({
  className,
  ...props
}: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-lg border border-surface-border bg-surface p-4",
        className,
      )}
      {...props}
    />
  )
}

export function SideDrawer({
  title,
  children,
  trigger,
  open,
  onOpenChange,
  modal = true,
  showOverlay = true,
  className,
}: {
  title: ReactNode
  children: ReactNode
  trigger?: ReactElement
  open?: boolean
  onOpenChange?: (open: boolean) => void
  modal?: boolean
  showOverlay?: boolean
  className?: string
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={modal}>
      {trigger && <SheetTrigger asChild>{trigger}</SheetTrigger>}
      <SheetContent
        data-workspace=""
        aria-modal={modal || undefined}
        aria-describedby={undefined}
        showOverlay={showOverlay}
        onInteractOutside={
          modal ? undefined : (event) => event.preventDefault()
        }
        className={cn(
          "inset-y-auto top-0 h-dvh w-[92vw] gap-0 border-surface-border bg-surface shadow-[-8px_0_28px_rgba(15,23,42,0.10)] sm:max-w-[440px]",
          !modal && "top-12 h-[calc(100dvh-3rem)] sm:max-w-[380px]",
          className,
        )}
      >
        <SheetHeader className="min-h-12 shrink-0 justify-center border-surface-border border-b py-2.5 pr-12">
          <SheetTitle className="text-sm">{title}</SheetTitle>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </SheetContent>
    </Sheet>
  )
}

export function HelpTip({
  label,
  content,
}: {
  label: string
  content: ReactNode
}) {
  const [open, setOpen] = useState(false)

  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label={label}
          className="size-5 rounded-full border-surface-border text-xs text-muted-foreground shadow-none"
          onClick={(event) => {
            event.preventDefault()
            setOpen(true)
          }}
        >
          ?
        </Button>
      </TooltipTrigger>
      <TooltipContent
        sideOffset={6}
        collisionPadding={12}
        className="max-w-[min(18rem,calc(100vw-24px))] text-pretty"
      >
        {content}
      </TooltipContent>
    </Tooltip>
  )
}
