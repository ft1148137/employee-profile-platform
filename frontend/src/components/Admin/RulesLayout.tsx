import type { ReactNode } from "react"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"

export function RulesLayout({
  children,
  summary,
  history,
}: {
  children: ReactNode
  summary: [string, ReactNode][]
  history?: ReactNode
}) {
  return (
    <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,13fr)_minmax(0,7fr)]">
      <div className="min-w-0 space-y-3">{children}</div>
      <aside aria-label="规则摘要与记录" className="min-w-0 space-y-3">
        <SectionCard aria-label="当前生效摘要" className="p-0">
          <h2 className="border-b px-4 py-2.5 text-sm font-semibold">
            当前生效摘要
          </h2>
          <dl className="space-y-2 px-4 py-3 text-sm">
            {summary.map(([label, value]) => (
              <div
                key={label}
                className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5"
              >
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 font-medium wrap-anywhere">{value}</dd>
              </div>
            ))}
          </dl>
        </SectionCard>
        {history}
      </aside>
    </div>
  )
}
