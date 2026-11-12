import { ChevronRight, MessagesSquare } from "lucide-react"
import { type ReactNode, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useIsMobile } from "@/hooks/useMobile"

export function AssistantLayout({
  label,
  title,
  conversations,
  children,
}: {
  label: string
  title: string
  conversations: (close: () => void) => ReactNode
  children: ReactNode
}) {
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  return (
    <div className="flex h-full min-h-0">
      {isMobile ? (
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="w-80 gap-0 p-0 [&_header]:pr-12">
            <SheetHeader className="sr-only">
              <SheetTitle>会话</SheetTitle>
              <SheetDescription>选择{label}会话</SheetDescription>
            </SheetHeader>
            {conversations(() => setOpen(false))}
          </SheetContent>
        </Sheet>
      ) : (
        <aside className="w-80 shrink-0 border-r">
          {conversations(() => setOpen(false))}
        </aside>
      )}
      <section className="flex min-w-0 flex-1 flex-col" aria-label={label}>
        <header className="flex h-16 shrink-0 items-center gap-3 border-b px-4 md:px-6">
          {isMobile && (
            <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
              <MessagesSquare className="size-4" />
              会话
            </Button>
          )}
          <nav
            aria-label="面包屑"
            className="flex min-w-0 items-center gap-2 text-sm"
          >
            <h1 className="shrink-0 font-medium">{label}</h1>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            <span
              aria-current="page"
              className="truncate text-muted-foreground"
            >
              {title}
            </span>
          </nav>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </section>
    </div>
  )
}
