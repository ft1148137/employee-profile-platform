import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"
import {
  type CapabilityPublic,
  CapacityService,
  type DepartmentPublic,
  type ProjectInput,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  capabilityLevels,
  DemandWorkFields,
  demandWork,
  useDemandOptions,
} from "./DemandFields"

type DraftRole = { id: number; name: string }

const selectStyle = "h-10 w-full rounded-md border bg-background px-3"
const labelStyle = "block space-y-2 text-sm font-medium"

function rolePrefix(id: number) {
  return `role-${id}-`
}

export function ProjectCreate({
  orgId,
  back,
  created,
}: {
  orgId: string
  back: () => void
  created: (id: string) => void
}) {
  const [roles, setRoles] = useState<DraftRole[]>([{ id: 0, name: "" }])
  const nextRole = useRef(1)
  const [requesting, setRequesting] = useState("")
  const [error, setError] = useState("")
  const options = useDemandOptions(orgId)
  const cache = useQueryClient()
  const submit = useMutation({
    mutationFn: (body: ProjectInput) =>
      CapacityService.submitProject({ path: { org_id: orgId }, body }),
    onSuccess: ({ data }) => {
      void cache.invalidateQueries({ queryKey: ["capacity", orgId] })
      created(data.id)
    },
  })
  if (options.isPending) return <ProfileLoading />
  if (options.isError) return <ProfileError retry={() => options.refetch()} />

  return (
    <div className="max-w-4xl space-y-5">
      <Button variant="outline" onClick={back} disabled={submit.isPending}>
        返回项目列表
      </Button>
      <h2 className="text-xl font-semibold">新建项目需求</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          const data = new FormData(event.currentTarget)
          const read = (name: string) => String(data.get(name) ?? "")
          const title = read("title").trim()
          const body: ProjectInput = {
            title,
            description: read("description").trim(),
            requesting_department_id: requesting,
            roles: roles.map(({ id, name }) => {
              const prefix = rolePrefix(id)
              const required = data.getAll(`${prefix}required`)
              const levels = data.getAll(`${prefix}extra-level`)
              return {
                ...demandWork(data, prefix),
                role_name: name.trim(),
                target_department_id: read(`${prefix}department`),
                capability_key: read(`${prefix}capability`),
                minimum_level: read(`${prefix}level`) || null,
                extra_capabilities: data
                  .getAll(`${prefix}extra`)
                  .map((key, index) => ({
                    key: String(key),
                    minimum_level: String(levels[index] ?? "") || null,
                    required: required.includes(String(index)),
                  })),
                depends_on: data
                  .getAll(`${prefix}dependency`)
                  .map((parent) =>
                    roles.findIndex((role) => role.id === Number(parent)),
                  ),
              }
            }),
          }
          const invalid = body.roles.findIndex((role) => {
            const keys = [
              role.capability_key,
              ...(role.extra_capabilities ?? []).map((extra) => extra.key),
            ]
            return (
              !role.role_name ||
              !role.description ||
              role.window_end < role.window_start ||
              new Set(keys).size !== keys.length
            )
          })
          if (!title || invalid >= 0) {
            submit.reset()
            setError(
              !title
                ? "请填写项目名称。"
                : `请检查第 ${invalid + 1} 个角色的名称、说明、时间窗口和能力。能力不能重复。`,
            )
            return
          }
          setError("")
          submit.mutate(body)
        }}
      >
        <fieldset disabled={submit.isPending} className="space-y-5">
          <div className="grid gap-4 rounded-xl border p-5 sm:grid-cols-2">
            <label className={labelStyle} htmlFor="project-title">
              <span>项目名称</span>
              <Input id="project-title" name="title" required maxLength={100} />
            </label>
            <label className={labelStyle}>
              <span>需求方部门</span>
              <select
                className={selectStyle}
                required
                value={requesting}
                onChange={(event) => setRequesting(event.target.value)}
              >
                <option value="">请选择</option>
                {options.data.requesting.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={`${labelStyle} sm:col-span-2`}>
              <span>项目说明（选填）</span>
              <textarea
                name="description"
                maxLength={2000}
                className="min-h-20 w-full rounded-md border bg-background p-3"
              />
            </label>
          </div>
          {roles.map((role, index) => (
            <RoleFields
              key={role.id}
              role={role}
              index={index}
              previous={roles.slice(0, index)}
              departments={options.data.departments.filter(
                (department) => department.id !== requesting,
              )}
              capabilities={options.data.capabilities}
              rename={(name) =>
                setRoles((rows) =>
                  rows.map((row) =>
                    row.id === role.id ? { ...row, name } : row,
                  ),
                )
              }
              remove={
                roles.length > 1
                  ? () =>
                      setRoles((rows) =>
                        rows.filter((row) => row.id !== role.id),
                      )
                  : undefined
              }
            />
          ))}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <SetupError error={submit.error} />
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={roles.length >= 12}
              onClick={() => {
                const id = nextRole.current++
                setRoles((rows) => [...rows, { id, name: "" }])
              }}
            >
              添加角色
            </Button>
            <Button type="submit">
              {submit.isPending ? "正在提交…" : "提交项目需求"}
            </Button>
          </div>
        </fieldset>
      </form>
    </div>
  )
}

function RoleFields({
  role,
  index,
  previous,
  departments,
  capabilities,
  rename,
  remove,
}: {
  role: DraftRole
  index: number
  previous: DraftRole[]
  departments: DepartmentPublic[]
  capabilities: CapabilityPublic[]
  rename: (name: string) => void
  remove?: () => void
}) {
  const [extras, setExtras] = useState<number[]>([])
  const nextExtra = useRef(0)
  const prefix = rolePrefix(role.id)
  return (
    <fieldset className="space-y-4 rounded-xl border px-5 pb-5">
      <legend className="px-2 font-semibold">角色 {index + 1}</legend>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className={labelStyle} htmlFor={`${prefix}name`}>
          <span>角色名称</span>
          <Input
            id={`${prefix}name`}
            value={role.name}
            onChange={(event) => rename(event.target.value)}
            maxLength={50}
            required
          />
        </label>
        <label className={labelStyle}>
          <span>目标部门</span>
          <select name={`${prefix}department`} className={selectStyle} required>
            <option value="">请选择</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
        </label>
        <CapabilityFields
          capabilities={capabilities}
          name={`${prefix}capability`}
          level={`${prefix}level`}
          label="主能力"
        />
        <DemandWorkFields prefix={prefix} />
      </div>
      {extras.map((id, extraIndex) => (
        <fieldset
          key={id}
          className="grid items-end gap-3 rounded-lg bg-muted/40 p-3 sm:grid-cols-2"
        >
          <legend className="px-2 text-sm font-medium">
            额外能力 {extraIndex + 1}
          </legend>
          <CapabilityFields
            capabilities={capabilities}
            name={`${prefix}extra`}
            level={`${prefix}extra-level`}
            label="额外能力"
          />
          <label className="flex min-h-9 items-center gap-2 text-sm">
            <input
              type="checkbox"
              name={`${prefix}required`}
              value={extraIndex}
            />
            必需（不勾选为可选）
          </label>
          <Button
            type="button"
            variant="ghost"
            className="justify-self-start"
            onClick={() =>
              setExtras((items) => items.filter((item) => item !== id))
            }
          >
            移除额外能力
          </Button>
        </fieldset>
      ))}
      <Button
        type="button"
        variant="outline"
        disabled={extras.length >= 5}
        onClick={() => {
          const id = nextExtra.current++
          setExtras((items) => [...items, id])
        }}
      >
        添加额外能力
      </Button>
      {previous.length > 0 && (
        <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
          <legend className="mb-2 text-sm font-medium">
            前置角色（完成后才开始）
          </legend>
          {previous.map((parent, position) => (
            <label key={parent.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name={`${prefix}dependency`}
                value={parent.id}
              />
              {parent.name.trim() || `角色 ${position + 1}`}
            </label>
          ))}
        </fieldset>
      )}
      {remove && (
        <Button type="button" variant="ghost" onClick={remove}>
          移除此角色
        </Button>
      )}
    </fieldset>
  )
}

function CapabilityFields({
  capabilities,
  name,
  level,
  label,
}: {
  capabilities: CapabilityPublic[]
  name: string
  level: string
  label: string
}) {
  const [key, setKey] = useState("")
  const levels = capabilityLevels(
    capabilities.find((entry) => entry.key === key)?.level_rubric,
  )
  return (
    <>
      <label className={labelStyle}>
        <span>{label}</span>
        <select
          name={name}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
          className={selectStyle}
        >
          <option value="">请选择</option>
          {capabilities.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.name}
            </option>
          ))}
        </select>
      </label>
      <label className={labelStyle}>
        <span>最低等级</span>
        <select key={key} name={level} className={selectStyle}>
          <option value="">不限</option>
          {levels.map((item) => (
            <option key={String(item.key)} value={String(item.key)}>
              {String(item.label || item.key)}
            </option>
          ))}
        </select>
      </label>
    </>
  )
}
