import type { CapacityReference } from "@/client"

export function DemandsReference({
  reference,
}: {
  reference: CapacityReference
}) {
  if ("limited" in reference) {
    return (
      <p>该部门具备这项能力的人较少，能否承接请提交需求，由对方部门经理答复</p>
    )
  }
  if (!("available" in reference)) {
    return (
      <p>
        {reference.department_name}达到要求的有 {reference.count}{" "}
        人；各人负荷只有对方部门经理能看到
      </p>
    )
  }
  return (
    <p>
      {reference.department_name}达到要求的有 {reference.count} 人：有余量{" "}
      {reference.available}、基本满 {reference.full}、超负荷{" "}
      {reference.overloaded}、未确认 {reference.unconfirmed}
    </p>
  )
}
