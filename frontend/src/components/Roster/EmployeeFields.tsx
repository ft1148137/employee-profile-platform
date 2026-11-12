import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useId } from "react"
import { type OrganizationPublic, OrgsService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Checkbox } from "@/components/ui/checkbox"

export const employeeFieldLabels = {
  job_family: "岗位序列",
  work_location: "工作地点",
  hire_date: "入职日期",
} as const

export function EmployeeFields({ org }: { org: OrganizationPublic }) {
  const client = useQueryClient()
  const id = useId()
  const fields = org.employee_fields ?? []
  const save = useMutation({
    mutationFn: (fields: NonNullable<OrganizationPublic["employee_fields"]>) =>
      OrgsService.updateEmployeeFields({
        path: { org_id: org.id },
        body: { fields },
      }),
    onSuccess: () => client.invalidateQueries(),
  })
  return (
    <section
      aria-label="员工档案字段"
      className="space-y-4 rounded-xl border p-5"
    >
      <div>
        <h2 className="text-lg font-semibold">员工档案字段</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          这些字段不进入画像，不参与画像起草
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          开启后可通过单人增改或管理助手维护；关闭只隐藏，保留已有值。切换后需要重新预览名单。
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {Object.entries(employeeFieldLabels).map(([key, label]) => {
          const field = key as keyof typeof employeeFieldLabels
          return (
            <label
              key={field}
              htmlFor={`${id}-${field}`}
              className="flex items-center gap-3 rounded-lg bg-muted/30 p-3 text-sm"
            >
              <Checkbox
                id={`${id}-${field}`}
                role="switch"
                checked={fields.includes(field)}
                disabled={save.isPending}
                onCheckedChange={(checked) =>
                  save.mutate(
                    checked
                      ? [...fields, field]
                      : fields.filter((f) => f !== field),
                  )
                }
              />
              {label}
            </label>
          )
        })}
      </div>
      <SetupError error={save.error} />
    </section>
  )
}

export function tenureLabel(hireDate: string): string {
  const [year, month, day] = hireDate.split("-").map(Number)
  const today = new Date()
  const anniversaryPending =
    today.getMonth() + 1 < month ||
    (today.getMonth() + 1 === month && today.getDate() < day)
  const years = today.getFullYear() - year - Number(anniversaryPending)
  return years < 1 ? "不满 1 年" : `入职 ${years} 年`
}
