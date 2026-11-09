import { useQuery } from "@tanstack/react-query"
import { AdminService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

const levels = {
  ok: { label: "正常", color: "border-l-emerald-600" },
  off: { label: "未开启", color: "border-l-muted-foreground" },
  warn: { label: "需要留意", color: "border-l-amber-500" },
  error: { label: "需要处理", color: "border-l-destructive" },
}

export function TodayPanel({ orgId }: { orgId: string }) {
  const overview = useQuery({
    queryKey: ["admin-overview", orgId],
    queryFn: async () =>
      (await AdminService.getOverview({ path: { org_id: orgId } })).data,
    refetchInterval: 30000,
  })
  return (
    <section className="space-y-4" aria-label="今天要处理的事">
      <h2 className="text-2xl font-semibold">今天要处理的事</h2>
      <SetupError error={overview.error} />
      {overview.isError && (
        <Button variant="outline" onClick={() => overview.refetch()}>
          重新读取
        </Button>
      )}
      {overview.isPending && <p role="status">正在读取今天的状态…</p>}
      {overview.data && (
        <>
          <div className="grid items-start gap-3 lg:grid-cols-2">
            {overview.data.status_cards.map((card) => (
              <Card
                key={card.key}
                role="article"
                aria-label={card.title}
                className={`gap-2 rounded-md border-l-4 p-4 shadow-none ${levels[card.level].color}`}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  <h3 className="font-semibold">{card.title}</h3>
                  <span className="text-xs text-muted-foreground">
                    {levels[card.level].label}
                  </span>
                  <p>{card.summary}</p>
                </div>
                {(card.level === "warn" || card.level === "error") && (
                  <>
                    <ul className="list-disc pl-5 text-sm text-muted-foreground">
                      {card.fix_steps.map((step) => (
                        <li key={step}>{step}</li>
                      ))}
                    </ul>
                    <Button variant="link" asChild className="self-start px-0">
                      <a href={card.page}>去处理</a>
                    </Button>
                  </>
                )}
              </Card>
            ))}
          </div>
          {overview.data.todos.length === 0 && (
            <p className="py-4 text-muted-foreground">今天没有要处理的事</p>
          )}
          <ul className="divide-y rounded-md border px-4">
            {overview.data.todos.map((todo) => (
              <li
                key={todo.kind}
                className="flex items-center justify-between gap-4 py-2"
              >
                <span>{todo.title}</span>
                <Button variant="link" asChild>
                  <a href={todo.page}>去处理</a>
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
