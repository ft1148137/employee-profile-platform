import type { CapacityReference } from "@/client"

export function DemandsReference({
  reference,
}: {
  reference: CapacityReference
}) {
  return (
    <p>
      {reference.department_name}达到要求的有 {reference.count} 人：有余量{" "}
      {reference.available}、基本满 {reference.full}、超负荷{" "}
      {reference.overloaded}、未确认 {reference.unconfirmed}
    </p>
  )
}
