import type { AccountPublic, EmploymentStatus, Role } from "@/client"

export type AccountStatus = AccountPublic["status"]

export const accountLabels: Record<AccountStatus, string> = {
  none: "还没有账号",
  pending_first_login: "待首次登录",
  active: "正常",
  disabled: "已停用",
}
export const employmentLabels: Record<EmploymentStatus, string> = {
  active: "在职",
  inactive: "已离职",
}
export const roleLabels: Record<Role, string> = {
  org_admin: "企业管理员",
  dept_manager: "部门经理",
}
