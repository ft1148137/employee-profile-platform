import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/assistant")({
  validateSearch: (
    search: Record<string, unknown>,
  ): { prompt?: string; session?: string } => ({
    prompt: typeof search.prompt === "string" ? search.prompt : undefined,
    session: typeof search.session === "string" ? search.session : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({
      to: "/assistant",
      search: { mode: "admin", ...search },
      replace: true,
    })
  },
})
