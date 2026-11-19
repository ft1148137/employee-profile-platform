import { hashKey, useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import {
  CapabilitiesService,
  OrgsService,
  PeopleSearchService,
  type peopleSearchSearchPeopleData,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { dateLabel, record } from "./fields"
import { labels } from "./labels"
import { StatusBadge } from "./StatusBadge"

type Filters = peopleSearchSearchPeopleData["query"]

const statuses = ["confirmed", "tentative", "disputed"] as const
const pageSize = 50

export function PeopleSearchFallback({
  orgId,
  initiallyOpen,
  isOrgAdmin,
  managedDepartmentIds,
}: {
  orgId: string
  initiallyOpen: boolean
  isOrgAdmin: boolean
  managedDepartmentIds: string[]
}) {
  const [filters, setFilters] = useState<Filters>({ capability_key: "" })
  const [query, setQuery] = useState<Filters>()
  const path = { org_id: orgId }
  const capabilities = useQuery({
    queryKey: ["capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path })).data,
  })
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () => (await OrgsService.readDepartments({ path })).data,
  })
  const results = useQuery({
    queryKey: [
      "people-search",
      orgId,
      isOrgAdmin ? "org_admin" : managedDepartmentIds,
      query,
    ],
    queryFn: async () =>
      (await PeopleSearchService.searchSearchPeople({ path, query: query! }))
        .data,
    enabled: !!query,
  })
  const rubric = capabilities.data?.data.find(
    (capability) => capability.key === filters.capability_key,
  )?.level_rubric
  const levels =
    record(rubric) && Array.isArray(rubric.levels)
      ? rubric.levels.filter(record)
      : []
  const departmentOptions = departments.data?.data.filter(
    ({ id }) => isOrgAdmin || managedDepartmentIds.includes(id),
  )
  const fields = [
    {
      key: "capability_key",
      label: "能力（必选）",
      options: capabilities.data?.data.map(({ key, name }) => ({
        key,
        label: name,
      })),
    },
    {
      key: "minimum_level",
      label: "最低等级",
      options: levels.map(({ key, label }) => ({
        key: String(key),
        label: String(label || key),
      })),
    },
    {
      key: "department_id",
      label: "部门（含下级）",
      options: departmentOptions?.map(({ id, name }) => ({
        key: id,
        label: name,
      })),
    },
    {
      key: "statuses",
      label: "判断状态",
      options: statuses.map((key) => ({
        key,
        label: labels.assessment_status[key].label,
      })),
    },
  ] as const
  const skip = query?.skip ?? 0
  const error = capabilities.error || departments.error || results.error

  return (
    <details
      open={initiallyOpen}
      className="max-h-[60%] shrink-0 overflow-y-auto border-b bg-muted/20 px-4 py-3 md:px-6"
    >
      <summary className="cursor-pointer text-sm font-semibold">
        模型暂未启用 · 按能力找人（只读筛选）
      </summary>
      <form
        aria-label="按能力找人（只读筛选）"
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          const search = { ...filters, skip: 0, limit: pageSize }
          if (hashKey([search]) === hashKey([query])) void results.refetch()
          else setQuery(search)
        }}
      >
        {fields.map(({ key, label, options }) => (
          <div key={key} className="min-w-36 flex-1 space-y-1">
            <label
              htmlFor={`people-search-${key}`}
              className="text-xs font-medium"
            >
              {label}
            </label>
            <select
              id={`people-search-${key}`}
              className="block h-9 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-50"
              value={
                (key === "statuses" ? filters.statuses?.[0] : filters[key]) ??
                ""
              }
              required={key === "capability_key"}
              disabled={key === "minimum_level" && levels.length === 0}
              onChange={(event) => {
                const value = event.target.value
                if (key === "statuses") {
                  setFilters({
                    ...filters,
                    statuses: statuses.filter((status) => status === value),
                  })
                } else if (key === "capability_key") {
                  setFilters({
                    ...filters,
                    capability_key: value,
                    minimum_level: undefined,
                  })
                } else {
                  setFilters({
                    ...filters,
                    [key]: value || undefined,
                  })
                }
              }}
            >
              <option value="">
                {key === "capability_key" ? "请选择能力" : "不限"}
              </option>
              {options?.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        ))}
        <Button
          type="submit"
          disabled={!filters.capability_key || results.isFetching}
        >
          查找
        </Button>
      </form>
      {error && (
        <div className="mt-3 space-y-2">
          <SetupError error={error} />
          <Button
            variant="outline"
            onClick={() => {
              if (capabilities.isError) void capabilities.refetch()
              if (departments.isError) void departments.refetch()
              if (results.isError) void results.refetch()
            }}
          >
            重试
          </Button>
        </div>
      )}
      {results.isFetching && (
        <p role="status" className="mt-3 text-sm">
          正在查找…
        </p>
      )}
      {results.data && (
        <section aria-label="找人结果" className="mt-3 space-y-2">
          <p role="status" className="text-sm text-muted-foreground">
            共 {results.data.count} 条已发布判断
          </p>
          {results.data.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              未知不等于不会：没有已发布判断的人不会出现在这里
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  {[
                    "姓名",
                    "部门",
                    "能力",
                    "等级",
                    "状态",
                    "陈述",
                    "证据数",
                    "最近证据时间",
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
                        className="font-medium text-primary underline-offset-4 hover:underline"
                        to="/employees/$employeeId"
                        params={{ employeeId: row.employee_id }}
                      >
                        {row.display_name}
                      </Link>
                    </TableCell>
                    <TableCell>{row.department_name || "—"}</TableCell>
                    <TableCell>{row.capability_name}</TableCell>
                    <TableCell>{row.level_name || "未定级"}</TableCell>
                    <TableCell>
                      <StatusBadge
                        kind="assessment_status"
                        value={row.status}
                      />
                    </TableCell>
                    <TableCell className="min-w-48 whitespace-normal">
                      {row.statement}
                    </TableCell>
                    <TableCell>{row.evidence_count}</TableCell>
                    <TableCell>{dateLabel(row.latest_evidence_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {query && results.data.count > pageSize && (
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={skip === 0}
                onClick={() => setQuery({ ...query, skip: skip - pageSize })}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={skip + pageSize >= results.data.count}
                onClick={() => setQuery({ ...query, skip: skip + pageSize })}
              >
                下一页
              </Button>
            </div>
          )}
        </section>
      )}
    </details>
  )
}
