import type { DepartmentPublic } from "@/client"

export function departmentOptions(departments: DepartmentPublic[]) {
  const byId = new Map(
    departments.map((department) => [department.id, department]),
  )
  return departments
    .map((department) => {
      const names = [department.name]
      const visited = new Set([department.id])
      let parent = department.parent_department_id
      while (parent && !visited.has(parent)) {
        visited.add(parent)
        const ancestor = byId.get(parent)
        if (!ancestor) break
        names.unshift(ancestor.name)
        parent = ancestor.parent_department_id
      }
      return { id: department.id, name: names.join(" / ") }
    })
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
}
