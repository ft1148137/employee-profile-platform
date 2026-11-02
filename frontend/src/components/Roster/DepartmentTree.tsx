import type { DepartmentNode } from "@/client"

export function DepartmentTree({ nodes }: { nodes: DepartmentNode[] }) {
  return (
    <ul className="space-y-2 border-l pl-4 text-sm">
      {nodes.map((node) => (
        <li key={node.path.join("/")}>
          <span>
            {node.name} · {node.employee_count} 人{node.is_new && " · 新建"}
          </span>
          {!!node.children?.length && <DepartmentTree nodes={node.children} />}
        </li>
      ))}
    </ul>
  )
}
