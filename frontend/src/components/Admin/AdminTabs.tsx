import type { ComponentType } from "react"
import { OrgState } from "@/components/Profile/OrgState"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOrg } from "@/hooks/useOrg"

export function tabSearch(search: Record<string, unknown>) {
  return { tab: typeof search.tab === "string" ? search.tab : undefined }
}

export function AdminTabs({
  title,
  description,
  tabs,
  tab,
  onTabChange,
  adminOnly = true,
}: {
  title: string
  description: string
  tabs: { value: string; title: string; component: ComponentType }[]
  tab: string | undefined
  onTabChange: (value: string) => void
  adminOnly?: boolean
}) {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (adminOnly && !roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  const selected = tabs.some((item) => item.value === tab) ? tab : tabs[0].value
  return (
    <div className="min-w-0 space-y-6">
      <header>
        <p className="text-xs tracking-widest text-muted-foreground">管理</p>
        <h1 className="mt-2 text-3xl font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
      </header>
      <Tabs key={org.id} value={selected} onValueChange={onTabChange}>
        <TabsList aria-label={title} className="h-auto flex-wrap justify-start">
          {tabs.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabs.map((item) => (
          <TabsContent key={item.value} value={item.value}>
            <item.component />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
