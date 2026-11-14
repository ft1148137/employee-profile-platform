import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { useState } from "react"
import {
  CapabilitiesService,
  OrgsService,
  PeopleSearchService,
  type peopleSearchSearchPeopleData,
} from "@/client"
import { dateLabel, record } from "@/components/Profile/fields"
import { labels } from "@/components/Profile/labels"
import { OrgState } from "@/components/Profile/OrgState"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/find-people")({
  component: FindPeoplePage,
  head: () => ({ meta: [{ title: "找人 - 员工画像平台" }] }),
})

type Filters = peopleSearchSearchPeopleData["query"]

function FindPeoplePage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin") && !roles.includes("dept_manager"))
    return <p role="alert">没有权限使用找人，需要企业管理员或部门经理权限</p>
  return <PeopleSearch key={org.id} orgId={org.id} />
}

function PeopleSearch({ orgId }: { orgId: string }) {
  const { org, roles, managedDepartmentIds } = useOrg()
  const [filters, setFilters] = useState<Filters>({ capability_key: "" })
  const [query, setQuery] = useState<Filters>()
  const download = useMutation({
    mutationFn: async (format: "csv" | "json") => {
      const { data } = await PeopleSearchService.searchExportProfiles({
        path: { org_id: orgId },
        query: { format },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("画像导出失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = `已发布画像.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  const capabilities = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const results = useQuery({
    queryKey: ["people-search", orgId, query],
    queryFn: async () =>
      (
        await PeopleSearchService.searchSearchPeople({
          path: { org_id: orgId },
          query: query!,
        })
      ).data,
    enabled: !!query,
  })
  const rubric = capabilities.data?.data.find(
    (c) => c.key === filters.capability_key,
  )?.level_rubric
  const levels =
    record(rubric) && Array.isArray(rubric.levels)
      ? rubric.levels.filter(record)
      : []
  const departmentOptions =
    departments.data?.data.filter(
      (d) => roles.includes("org_admin") || managedDepartmentIds.includes(d.id),
    ) ?? []
  const error = results.error || capabilities.error || departments.error
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">按能力找人</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            在你的可见范围内，查找有已发布判断的在职员工。
          </p>
        </div>
        <section aria-label="导出已发布画像" className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {(["csv", "json"] as const).map((format) => (
              <Button
                key={format}
                variant="outline"
                disabled={download.isPending}
                onClick={() => download.mutate(format)}
              >
                导出已发布画像（{format.toUpperCase()}）
              </Button>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">
            导出本人可见的全部已发布判断，不受页面筛选影响。
          </p>
          <SetupError error={download.error} />
        </section>
      </div>
      <form
        className="space-y-4 rounded-xl border bg-muted/20 p-5"
        onSubmit={(event) => {
          event.preventDefault()
          setQuery({ ...filters, skip: 0 })
        }}
      >
        <div className="grid gap-4 md:grid-cols-3">
          {[
            {
              key: "capability_key" as const,
              label: "能力（必选）",
              options:
                capabilities.data?.data.map((c) => ({
                  key: c.key,
                  label: c.name,
                })) ?? [],
            },
            {
              key: "minimum_level" as const,
              label: "最低等级",
              options: levels.map((l) => ({
                key: String(l.key),
                label: String(l.label || l.key),
              })),
            },
            {
              key: "department_id" as const,
              label: "部门（含下级）",
              options: departmentOptions.map((d) => ({
                key: d.id,
                label: d.name,
              })),
            },
          ].map(({ key, label, options }) => (
            <div key={key} className="space-y-2">
              <label htmlFor={key} className="text-sm font-medium">
                {label}
              </label>
              <Select
                value={filters[key] || "all"}
                onValueChange={(value) =>
                  setFilters({
                    ...filters,
                    [key]: value === "all" ? undefined : value,
                    ...(key === "capability_key"
                      ? { minimum_level: undefined }
                      : {}),
                  })
                }
              >
                <SelectTrigger id={key} aria-label={label} className="w-full">
                  <SelectValue placeholder="请选择" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {key === "capability_key" ? "请选择能力" : "不限"}
                  </SelectItem>
                  {options.map((o) => (
                    <SelectItem key={o.key} value={o.key}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
          {(
            [
              ["evidence_from", "最近证据起始日期", "date"],
              ["evidence_to", "最近证据结束日期", "date"],
              ...(org?.employee_fields?.includes("job_family")
                ? [["job_family", "岗位序列", "text"] as const]
                : []),
              ...(org?.employee_fields?.includes("work_location")
                ? [["work_location", "工作地点", "text"] as const]
                : []),
              ...(org?.employee_fields?.includes("hire_date")
                ? [
                    ["hire_date_from", "入职起始日期", "date"] as const,
                    ["hire_date_to", "入职结束日期", "date"] as const,
                  ]
                : []),
            ] as const
          ).map(([key, label, type]) => (
            <label
              key={key}
              htmlFor={key}
              className="space-y-2 text-sm font-medium"
            >
              {label}
              <Input
                id={key}
                type={type}
                value={filters[key] || ""}
                onChange={(e) =>
                  setFilters({ ...filters, [key]: e.target.value || undefined })
                }
              />
            </label>
          ))}
        </div>
        <fieldset className="flex flex-wrap gap-4">
          <legend className="mb-2 text-sm font-medium">
            判断状态（不选则不限）
          </legend>
          {(["confirmed", "tentative", "disputed"] as const).map((status) => (
            <label
              key={status}
              htmlFor={status}
              className="flex items-center gap-2 text-sm"
            >
              <Checkbox
                id={status}
                checked={filters.statuses?.includes(status) ?? false}
                onCheckedChange={(checked) =>
                  setFilters({
                    ...filters,
                    statuses: checked
                      ? [...(filters.statuses ?? []), status]
                      : filters.statuses?.filter((s) => s !== status),
                  })
                }
              />
              {labels.assessment_status[status].label}
            </label>
          ))}
        </fieldset>
        <div className="flex flex-wrap items-center gap-4">
          <label
            htmlFor="include-ungraded"
            className="flex items-center gap-2 text-sm"
          >
            <Checkbox
              id="include-ungraded"
              checked={filters.include_ungraded ?? false}
              onCheckedChange={(v) =>
                setFilters({ ...filters, include_ungraded: v === true })
              }
            />
            包含未定级
          </label>
          <label
            htmlFor="department-capability"
            className="flex items-center gap-2 text-sm"
          >
            <Checkbox
              id="department-capability"
              checked={filters.department_capability_only ?? false}
              onCheckedChange={(v) =>
                setFilters({
                  ...filters,
                  department_capability_only: v === true,
                })
              }
            />
            只看把这项能力列为对应能力的部门
          </label>
          <Button
            type="submit"
            disabled={!filters.capability_key || results.isFetching}
          >
            查找
          </Button>
        </div>
      </form>
      {error && (
        <p role="alert">
          {isAxiosError(error) &&
          typeof error.response?.data?.detail === "string"
            ? error.response.data.detail
            : "加载失败，请重试"}
          <Button
            variant="outline"
            onClick={() => {
              void results.refetch()
              void capabilities.refetch()
              void departments.refetch()
            }}
          >
            重试
          </Button>
        </p>
      )}
      {query && results.isPending && <p role="status">正在查找…</p>}
      {results.data && (
        <section aria-label="找人结果" className="space-y-4">
          <p role="status" className="text-sm text-muted-foreground">
            共 {results.data.count} 条已发布判断
          </p>
          {results.data.count === 0 ? (
            <p className="rounded-xl border border-dashed p-8 text-center text-muted-foreground">
              未知不等于不会：没有已发布判断的人不会出现在这里
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  {[
                    "姓名",
                    "工号",
                    "部门",
                    "能力",
                    "陈述",
                    "等级",
                    "状态",
                    "证据数",
                    "最近证据时间",
                    "批准人",
                    "批准时间",
                  ].map((label) => (
                    <TableHead key={label}>{label}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.data.data.map((row) => (
                  <TableRow key={row.employee_id}>
                    <TableCell>
                      <Link
                        className="font-medium text-primary hover:underline"
                        to="/employees/$employeeId"
                        params={{ employeeId: row.employee_id }}
                      >
                        {row.display_name}
                      </Link>
                    </TableCell>
                    <TableCell>{row.employee_code || "—"}</TableCell>
                    <TableCell>{row.department_name || "—"}</TableCell>
                    <TableCell>{row.capability_name}</TableCell>
                    <TableCell className="min-w-64 max-w-md whitespace-normal">
                      {row.statement}
                    </TableCell>
                    <TableCell>{row.level_name || "未定级"}</TableCell>
                    <TableCell>
                      <StatusBadge
                        kind="assessment_status"
                        value={row.status}
                      />
                    </TableCell>
                    <TableCell>{row.evidence_count}</TableCell>
                    <TableCell>{dateLabel(row.latest_evidence_at)}</TableCell>
                    <TableCell>
                      {row.published_by.display_name || "系统"}
                    </TableCell>
                    <TableCell>{dateLabel(row.published_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <div className="flex items-center justify-end gap-3">
            <Button
              variant="outline"
              disabled={!query?.skip}
              onClick={() =>
                query &&
                setQuery({
                  ...query,
                  skip: Math.max(0, (query.skip ?? 0) - 50),
                })
              }
            >
              上一页
            </Button>
            <span className="text-sm">
              第 {Math.floor((query?.skip ?? 0) / 50) + 1} 页
            </span>
            <Button
              variant="outline"
              disabled={(query?.skip ?? 0) + 50 >= results.data.count}
              onClick={() =>
                query && setQuery({ ...query, skip: (query.skip ?? 0) + 50 })
              }
            >
              下一页
            </Button>
          </div>
        </section>
      )}
    </div>
  )
}
