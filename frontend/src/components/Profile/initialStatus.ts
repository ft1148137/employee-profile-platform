import type { InitialProfileStatus } from "@/client"

const initialStatusLabels: Record<InitialProfileStatus, string> = {
  waiting_source: "等待接入工作记录",
  no_records: "没有工作记录",
  waiting_draft: "等待起草",
  drafting: "起草中",
  pending_owner: "待本人确认",
  no_judgment: "暂未生成内容",
  failed: "起草失败",
  owner_reviewed: "本人已确认",
  direct_edited: "经理或管理员已直接修改",
}

export function initialStatusLabel(status: InitialProfileStatus | undefined) {
  return status ? initialStatusLabels[status] : "状态待确认"
}

export function initialStatusReason(
  status: InitialProfileStatus | undefined,
  owner: boolean,
) {
  const subject = owner ? "你" : "该员工"
  switch (status) {
    case undefined:
      return "暂时无法确认初始画像状态，可以联系管理员核对。"
    case "waiting_source":
      return "企业还没有接入工作记录，可以联系管理员接入 Jira 或导入 CSV。"
    case "no_records":
      return `还没有${subject}的工作记录，可能尚未关联工作账号，或关联后还没有记录。可以联系管理员核对。`
    case "waiting_draft":
      return `已有${subject}的工作记录，正在等待系统起草初始画像。`
    case "drafting":
      return `系统正在为${subject}起草初始画像，完成后会交给本人确认。`
    case "pending_owner":
      return `初始画像已起草，等待${subject}确认，确认或修改后才会提交审批。`
    case "no_judgment":
      return `现有记录暂不足以评估等级，这不代表不具备相关能力。`
    case "failed":
      return "初始画像起草失败，可以联系管理员重试。"
    case "owner_reviewed":
      return `初始画像已由${subject}确认。`
    case "direct_edited":
      return `画像由经理或管理员直接修改过，没有经过${subject}确认。`
  }
}
