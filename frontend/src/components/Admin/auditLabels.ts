import type { AuditEventPublic } from "@/client"

export const auditLabels: Record<string, string> = {
  "account.issued": "生成账号",
  "account.password_reset": "重置密码",
  "account.disabled": "停用账号",
  "account.enabled": "启用账号",
  "role.granted": "授予角色",
  "role.revoked": "撤销角色",
  "setup.initialized": "初始化企业",
  "setup.step_completed": "完成设置步骤",
  "setup.step_skipped": "跳过设置步骤",
  "org.renamed": "修改企业名称",
  "capability_schema.activated": "启用能力词典",
  "seed.loaded": "导入演示数据",
  "roster.imported": "导入员工名单",
}
export const actorLabels: Record<string, string> = {
  system_worker: "系统任务",
  agent: "画像助手",
  seed: "演示数据",
  cli: "命令行",
}
export const targetLabels: Record<string, string> = {
  user: "账号",
  role_grant: "角色",
  organization: "企业",
  capability_schema_version: "能力词典版本",
  setup_step: "设置步骤",
  roster_import: "名单导入",
}
const stepLabels: Record<string, string> = {
  org_profile: "企业信息与管理员账号",
  roster: "导入员工名单",
  capabilities: "选择能力词典",
  accounts: "生成员工账号",
}
const countLabels: Record<string, string> = {
  added: "新增",
  updated: "更新",
  transferred: "调岗",
  manager_changed: "变更上级",
  deactivated: "停用",
  reactivated: "恢复",
}
export function auditSummary({ action, details: d }: AuditEventPublic): string {
  if (action === "org.renamed") return `「${d.old_name}」改为「${d.new_name}」`
  if (action === "capability_schema.activated")
    return `${d.capability_count} 项能力`
  if (action === "setup.step_completed" || action === "setup.step_skipped")
    return `步骤：${stepLabels[String(d.step_key)] ?? d.step_key}`
  if (action === "roster.imported")
    return Object.entries(countLabels)
      .filter(([key]) => typeof d[key] === "number" && d[key] > 0)
      .map(([key, label]) => `${label} ${d[key]} 人`)
      .join("，")
  if (action === "account.password_reset" && d.source === "cli")
    return "在服务器上执行"
  return ""
}

export function localMidnight(
  date: string,
  nextDay = false,
): string | undefined {
  if (!date) return undefined
  const value = new Date(`${date}T00:00:00`)
  if (nextDay) value.setDate(value.getDate() + 1)
  return value.toISOString()
}
