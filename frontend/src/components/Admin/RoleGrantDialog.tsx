import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
  AccountsService,
  type Role,
  type RolePublic,
  RolesService,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { roleLabels } from "./labels"

export function RoleGrantDialog({
  orgId,
  departments,
  grants,
  onSuccess,
}: {
  orgId: string
  departments: { id: string; name: string }[]
  grants: RolePublic[]
  onSuccess: () => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [userId, setUserId] = useState("")
  const [role, setRole] = useState<Role>("dept_manager")
  const [departmentId, setDepartmentId] = useState("")
  const [message, setMessage] = useState("")
  const accounts = useQuery({
    queryKey: ["accounts", orgId, "role-picker", search.trim()],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: { q: search.trim(), employment_status: "active", limit: 100 },
        })
      ).data,
    enabled: open,
  })
  const mutation = useMutation({
    mutationFn: async () =>
      (
        await RolesService.grantRole({
          path: { org_id: orgId },
          body: {
            user_id: userId,
            role,
            department_id: role === "dept_manager" ? departmentId : null,
          },
        })
      ).data,
    onSuccess: (grant) => {
      setMessage(
        grants.some((item) => item.id === grant.id)
          ? "这个角色已经授予过"
          : "角色已授予",
      )
      onSuccess()
    },
  })
  function changeOpen(value: boolean) {
    if (mutation.isPending) return
    setOpen(value)
    setSearch("")
    setUserId("")
    setRole("dept_manager")
    setDepartmentId("")
    setMessage("")
    mutation.reset()
  }
  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <Button onClick={() => changeOpen(true)}>授予角色</Button>
      <DialogContent showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>授予角色</DialogTitle>
          <DialogDescription>
            为在职且已启用账号的员工授予管理角色。
          </DialogDescription>
        </DialogHeader>
        <Input
          aria-label="搜索姓名或工号"
          placeholder="搜索姓名或工号…"
          value={search}
          disabled={mutation.isPending}
          onChange={(event) => {
            setSearch(event.target.value)
            setUserId("")
          }}
        />
        <SetupError error={accounts.error} />
        <RoleSelect
          label="账号"
          value={userId}
          onChange={setUserId}
          disabled={mutation.isPending || accounts.isFetching}
          options={
            accounts.data?.data.flatMap((employee) =>
              employee.account && employee.account.status !== "disabled"
                ? [
                    {
                      id: employee.account.user_id,
                      name: `${employee.display_name}（${employee.account.username}）`,
                    },
                  ]
                : [],
            ) ?? []
          }
        />
        {accounts.data && accounts.data.count > 100 && (
          <p>仅显示前一百名员工，请输入姓名或工号缩小范围。</p>
        )}
        <RoleSelect
          label="角色"
          value={role}
          disabled={mutation.isPending}
          onChange={(value) => {
            if (value === "org_admin" || value === "dept_manager")
              setRole(value)
          }}
          options={Object.entries(roleLabels).map(([id, name]) => ({
            id,
            name,
          }))}
        />
        {role === "dept_manager" && (
          <RoleSelect
            label="部门"
            value={departmentId}
            onChange={setDepartmentId}
            disabled={mutation.isPending}
            options={departments}
          />
        )}
        <SetupError error={mutation.error} />
        {message && <p role="status">{message}</p>}
        <Button
          disabled={
            !userId ||
            (role === "dept_manager" && !departmentId) ||
            accounts.isFetching ||
            accounts.isError ||
            mutation.isPending
          }
          onClick={() => {
            setMessage("")
            mutation.mutate()
          }}
        >
          {mutation.isPending ? "正在授予…" : "确认授予"}
        </Button>
        <Button
          variant="outline"
          disabled={mutation.isPending}
          onClick={() => changeOpen(false)}
        >
          关闭
        </Button>
      </DialogContent>
    </Dialog>
  )
}

function RoleSelect({
  label,
  value,
  onChange,
  disabled,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
  options: { id: string; name: string }[]
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label}>
        <SelectValue placeholder={`请选择${label}`} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.id} value={option.id}>
            {option.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
