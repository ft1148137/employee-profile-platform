import type { RosterImportPreview } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { DepartmentTree } from "./DepartmentTree"

export const changeLabels = {
  added: "新增",
  updated: "信息变更",
  transferred: "调岗",
  manager_changed: "上级变化",
  deactivated: "停用",
  reactivated: "恢复在职",
}
const fieldLabels = {
  display_name: "姓名",
  title: "职位",
  email: "邮箱",
  department: "部门",
  manager: "上级",
}

export function RosterPreview({ preview }: { preview: RosterImportPreview }) {
  const errors = preview.issues.filter((issue) => issue.severity === "error")
  const warnings = preview.issues.filter(
    (issue) => issue.severity === "warning",
  )
  return (
    <section aria-label="名单预览" className="space-y-5">
      <h3 className="text-lg font-semibold">导入预览</h3>
      <details>
        <summary className="cursor-pointer">识别到的列</summary>
        <ul className="mt-2 text-sm">
          {Object.entries(preview.column_mapping).map(([from, to]) => (
            <li key={from}>
              {from} → {to}
            </li>
          ))}
        </ul>
      </details>
      {errors.length > 0 && (
        <div
          role="alert"
          className="rounded-lg border border-destructive p-4 text-destructive"
        >
          <h4 className="font-semibold">名单有错误，请改好后重新上传</h4>
          <DataTable
            data={errors}
            columns={[
              {
                accessorKey: "row_number",
                header: "行号",
                cell: ({ row }) => row.original.row_number ?? "文件",
              },
              { accessorKey: "column", header: "列" },
              { accessorKey: "message", header: "问题" },
            ]}
          />
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border bg-muted/40 p-4 text-sm">
          <h4 className="font-semibold">提醒</h4>
          <ul className="list-disc pl-5">
            {warnings.map((issue, index) => (
              <li key={`${index}:${issue.message}`}>
                {issue.row_number ? `第 ${issue.row_number} 行：` : ""}
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(changeLabels).map(([kind, label]) => (
          <details
            key={kind}
            className={`min-w-0 rounded-lg border p-3 ${kind === "deactivated" && preview.summary[kind] ? "border-destructive bg-destructive/5" : "bg-muted/20"}`}
          >
            <summary className="cursor-pointer font-medium">
              {label} {preview.summary[kind] ?? 0} 人
            </summary>
            <DataTable
              data={preview.changes.filter((item) => item.kind === kind)}
              columns={[
                { accessorKey: "employee_code", header: "工号" },
                { accessorKey: "display_name", header: "姓名" },
                {
                  id: "before",
                  header: "变化前",
                  cell: ({ row }) =>
                    (row.original.fields ?? [])
                      .map(
                        (f) => `${fieldLabels[f.field]}：${f.before ?? "无"}`,
                      )
                      .join("；") ||
                    (kind === "deactivated"
                      ? "在职"
                      : kind === "reactivated"
                        ? "停用"
                        : "无"),
                },
                {
                  id: "after",
                  header: "变化后",
                  cell: ({ row }) =>
                    (row.original.fields ?? [])
                      .map((f) => `${fieldLabels[f.field]}：${f.after ?? "无"}`)
                      .join("；") || label,
                },
              ]}
            />
          </details>
        ))}
        <details className="rounded-lg border bg-muted/20 p-3">
          <summary className="cursor-pointer font-medium">
            新建部门 {preview.created_departments.length} 个
          </summary>
          <ul className="mt-2 text-sm">
            {preview.created_departments.map((path) => (
              <li key={path.join("/")}>{path.join(" / ")}</li>
            ))}
          </ul>
        </details>
      </div>
      <div className="rounded-lg border p-4">
        <h4 className="mb-3 font-semibold">导入后的部门树</h4>
        <DepartmentTree nodes={preview.departments} />
      </div>
    </section>
  )
}
