import type { TurnResponse } from "@/client"

type Card = TurnResponse["messages"][number]
export type RequestCard = Extract<
  Card,
  { type: "preview_card" | "status_card" }
>

export function requestCardHistory(cards: Card[]) {
  const laterCards = new Map<
    string,
    { next: RequestCard; latest: RequestCard }
  >()
  const history = new Map<RequestCard, RequestCard>()
  for (const card of [...cards].reverse()) {
    if (card.type !== "status_card" && card.type !== "preview_card") continue
    const id = card.type === "preview_card" ? card.request_id : card.id
    const later = laterCards.get(id)
    if (later)
      history.set(
        card,
        card.type === "preview_card" && later.next.type === "preview_card"
          ? later.next
          : later.latest,
      )
    laterCards.set(id, { next: card, latest: later?.latest ?? card })
  }
  return history
}

export function previewHistoryLabel(
  card: Extract<RequestCard, { type: "preview_card" }>,
  later: RequestCard | undefined,
) {
  if (!later && card.actions.length) return undefined
  if (later?.type === "status_card") {
    if (later.status === "withdrawn") return "已撤回"
    if (later.status !== "draft" && later.status !== "pending_owner")
      return "已提交"
  }
  return "已过期"
}
