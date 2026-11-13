import type { AuditEventPublic } from "@/client"

export const auditLabels: Record<string, string> = {
  "profile.direct_edited": "直接修改员工画像",
  "appeal.open": "提交申诉",
  "appeal.upheld": "申诉维持原决定",
  "appeal.reopened": "申诉允许重新提交",
  "oauth.allowed": "允许 AI 助手授权",
  "oauth.denied": "拒绝 AI 助手授权",
  "oauth.revoked": "撤销 AI 助手授权",
  "mcp.tool_called": "外部 AI 助手调用员工工具",
  "question.answered": "回答待确认问题",
  "access_token.created": "生成个人令牌",
  "access_token.revoked": "吊销个人令牌",
  "mcp_setting.updated": "修改外部 AI 助手设置",
  "approval_policy.saved": "保存审批规则",
  "review_delegation.created": "设置代理审批人",
  "review_delegation.revoked": "撤销代理审批人",
  "change_request.policy_published": "按审批规则发布",
  "initial_profiles.handed_over": "初始画像批量送审（员工未过目）",
  "initial_profiles.queued": "安排初始画像起草",
  "initial_profiles.drafted": "生成初始画像",
  "incremental_profiles.drafted": "起草增量画像",
  "change_request.dictionary_changed": "词典调整后更新请求",
  "profile_import.created": "管理员导入画像",
  "change_request.submitted": "提交画像请求",
  "change_request.decided": "审批画像请求",
  "change_request.withdrawn": "撤回画像请求",
  "sync_run.succeeded": "同步完成",
  "sync_run.queued": "安排数据同步",
  "sync_run.failed": "同步失败",
  "source.jira_updated": "修改 Jira 连接",
  "external_identity.linked": "关联外部账号",
  "external_identity.unlinked": "解除外部账号关联",
  "model_setting.updated": "修改模型设置",
  "model_setting.chatgpt_connected": "连接 ChatGPT",
  "model_setting.chatgpt_disconnected": "断开 ChatGPT",
  "evidence.superseded": "更正证据",
  "evidence.retracted": "撤销证据",
  "account.issued": "生成账号",
  "account.password_reset": "重置密码",
  "account.disabled": "停用账号",
  "account.enabled": "启用账号",
  "role.granted": "授予角色",
  "role.revoked": "撤销角色",
  "setup.initialized": "初始化企业",
  "setup.step_completed": "完成设置步骤",
  "setup.step_skipped": "跳过设置步骤",
  "department.capabilities_updated": "修改部门对应能力",
  "department.capabilities_generated": "自动生成部门对应能力",
  "org.renamed": "修改企业名称",
  "org.employee_fields_updated": "修改员工档案字段开关",
  "capability_schema.activated": "启用能力词典",
  "capability_proposal.open": "撤销忽略能力建议",
  "capability_proposal.suggested": "员工建议新增能力",
  "capability_proposal.accepted": "将能力建议加入词典",
  "capability_proposal.dismissed": "忽略能力建议",
  "seed.loaded": "导入演示数据",
  "update_settings.updated": "修改持续更新提醒设置",
  "drafting_settings.updated": "修改自动起草设置",
  "drafting_note.updated": "修改起草补充说明",
  "bot_settings.updated": "修改机器人设置",
  "im_settings.updated": "修改 IM 入口设置",
  "im_binding.code_created": "生成 IM 绑定码",
  "im_binding.bound": "绑定 IM 账号",
  "im_binding.unbound": "解除 IM 绑定",
  "employee.auto_draft_changed": "修改本人自动起草选择",
  "account.email_changed": "修改账号邮箱",
  "roster.imported": "导入员工名单",
}
export const actorLabels: Record<string, string> = {
  system_worker: "系统任务",
  agent: "画像助手",
  seed: "演示数据",
  cli: "命令行",
}
export const targetLabels: Record<string, string> = {
  department: "部门",
  review_delegation: "代理审批",
  review_appeal: "画像申诉",
  approval_policy: "审批规则",
  im_binding: "IM 账号绑定",
  oauth_client: "AI 助手客户端",
  oauth_grant: "AI 助手授权",
  mcp_tool: "员工工具",
  pending_question: "待确认问题",
  personal_access_token: "个人令牌",
  mcp_setting: "外部 AI 助手设置",
  sync_run: "同步记录",
  profile_change_request: "画像变更请求",
  source_connection: "数据来源",
  external_identity: "外部账号",
  model_setting: "模型设置",
  evidence: "证据",
  employee: "员工",
  update_setting: "持续更新提醒设置",
  drafting_setting: "自动起草设置",
  drafting_note: "起草补充说明",
  bot_setting: "机器人设置",
  im_setting: "IM 入口设置",
  user: "账号",
  role_grant: "角色",
  organization: "企业",
  capability_proposal: "能力建议",
  capability_schema_version: "能力词典版本",
  setup_step: "设置步骤",
  roster_import: "名单导入",
}
const stepLabels: Record<string, string> = {
  org_profile: "企业信息与管理员账号",
  roster: "导入员工名单",
  capabilities: "选择能力词典",
  accounts: "生成员工账号",
  model: "配置模型",
  approval_policy: "审批规则",
  initial_profiles: "生成初始画像",
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
