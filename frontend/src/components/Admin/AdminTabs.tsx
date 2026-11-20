import type { ComponentType } from "react"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export function tabSearch(search: Record<string, unknown>): { tab?: string } {
  return { tab: typeof search.tab === "string" ? search.tab : undefined }
}

export function AdminSection({
  sections,
  tab,
  deniedMessage,
}: {
  sections: { value: string; title?: string; component: ComponentType }[]
  tab?: string
  deniedMessage?: string
}) {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (deniedMessage && !roles.includes("org_admin"))
    return <p role="alert">{deniedMessage}</p>
  const {
    title,
    component: Content,
    value,
  } = sections.find((section) => section.value === tab) ?? sections[0]
  return (
    <div className="min-w-0" key={`${org.id}:${value}`}>
      {title && <h1 className="sr-only">{title}</h1>}
      <Content />
    </div>
  )
}
