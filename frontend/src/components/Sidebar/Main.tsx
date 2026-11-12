import { Link, useLocation } from "@tanstack/react-router"
import type { LucideIcon } from "lucide-react"

export type Item = {
  icon: LucideIcon
  title: string
  path: string
  badge?: string
}

export const railItemClass =
  "relative flex h-12 w-full shrink-0 flex-col items-center justify-center gap-1 rounded-lg text-[10px] leading-3 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-2 focus-visible:outline-ring aria-current:bg-sidebar-accent aria-current:text-sidebar-accent-foreground"

export function Main({ items }: { items: Item[] }) {
  const { pathname } = useLocation()
  return items.map((item) => (
    <Link
      key={item.path}
      to={item.path}
      aria-label={item.title}
      aria-current={pathname === item.path ? "page" : undefined}
      className={railItemClass}
      title={item.title}
    >
      <item.icon className="size-5" aria-hidden="true" />
      <span className="whitespace-nowrap">{item.title}</span>
      {item.badge && (
        <span className="absolute right-0 top-0 rounded-full bg-primary px-1 text-[9px] leading-4 text-primary-foreground">
          {item.badge}
        </span>
      )}
    </Link>
  ))
}
