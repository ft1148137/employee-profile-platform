import { useQuery } from "@tanstack/react-query"
import {
  CapabilitiesService,
  CapacityService,
  EmployeesService,
} from "@/client"
import { record } from "@/components/Profile/fields"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"

export function useDemandOptions(orgId: string) {
  const { roles, employeeId, managedDepartmentIds } = useOrg()
  return useQuery({
    queryKey: ["capacity", orgId, "demand-options"],
    queryFn: async () => {
      const [departments, capabilities, self] = await Promise.all([
        CapacityService.listCapacityDepartments({ path: { org_id: orgId } }),
        CapabilitiesService.readCapabilities({ path: { org_id: orgId } }),
        employeeId
          ? EmployeesService.readEmployee({
              path: { org_id: orgId, employee_id: employeeId },
            })
          : null,
      ])
      return {
        departments: departments.data.data,
        capabilities: capabilities.data.data,
        ownDepartment: self?.data.department?.id,
      }
    },
    select: (options) => ({
      ...options,
      requesting: options.departments.filter(
        (d) =>
          roles.includes("org_admin") ||
          (managedDepartmentIds.length
            ? managedDepartmentIds.includes(d.id)
            : d.id === options.ownDepartment),
      ),
    }),
  })
}

export function capabilityLevels(rubric: unknown) {
  return record(rubric) && Array.isArray(rubric.levels)
    ? rubric.levels.filter(record)
    : []
}

export function demandWork(data: FormData, prefix = "") {
  return {
    headcount: Number(data.get(`${prefix}headcount`)),
    allocation_percent: Number(data.get(`${prefix}allocation`)),
    window_start: String(data.get(`${prefix}start`)),
    window_end: String(data.get(`${prefix}end`)),
    description: String(data.get(`${prefix}description`)).trim(),
  }
}

export function DemandWorkFields({ prefix = "" }: { prefix?: string }) {
  const idPrefix = prefix || "demand-"
  return (
    <>
      {[
        {
          name: "headcount",
          label: "人数",
          type: "number",
          min: 1,
          max: 50,
        },
        {
          name: "allocation",
          label: "每人投入比例（%）",
          type: "number",
          min: 10,
          max: 100,
        },
        { name: "start", label: "开始日期", type: "date" },
        { name: "end", label: "结束日期", type: "date" },
      ].map(({ name, label, ...input }) => (
        <label
          key={name}
          htmlFor={`${idPrefix}${name}`}
          className="space-y-2 text-sm font-medium"
        >
          <span>{label}</span>
          <Input
            id={`${idPrefix}${name}`}
            name={`${prefix}${name}`}
            {...input}
            required
          />
        </label>
      ))}
      <label className="space-y-2 text-sm font-medium sm:col-span-2">
        <span>需求说明</span>
        <textarea
          name={`${prefix}description`}
          required
          maxLength={2000}
          className="min-h-28 w-full rounded-md border bg-background p-3"
        />
      </label>
    </>
  )
}
