import type { InitialProfileStatus } from "@/client"

export const initialStatusLabels: Record<InitialProfileStatus, string> = {
  waiting_source: "等待接入工作记录",
  no_records: "没有工作记录",
  waiting_draft: "等待起草",
  drafting: "起草中",
  pending_owner: "待本人过目",
  no_judgment: "未生成判断",
  failed: "起草失败",
  done: "已处理",
}

export function initialStatusReason(
  status: InitialProfileStatus,
  owner: boolean,
) {
  const subject = owner ? "你" : "该员工"
  switch (status) {
    case "waiting_source":
      return "企业还没有接入工作记录，可以联系管理员接入 Jira 或导入 CSV。"
    case "no_records":
      return `还没有${subject}的工作记录，可能尚未关联工作账号，或关联后还没有记录。可以联系管理员核对。`
    case "waiting_draft":
      return `已有${subject}的工作记录，正在等待系统起草初始画像。`
    case "drafting":
      return `系统正在为${subject}起草初始画像，完成后会交给本人过目。`
    case "pending_owner":
      return `初始画像已起草，等待${subject}过目，确认或修改后才会提交审核。`
    case "no_judgment":
      return `已有${subject}的工作记录，但起草时定不了等级，没有生成判断。这不表示${subject}不会这项能力。`
    case "failed":
      return "初始画像起草失败，可以联系管理员重试。"
    case "done":
      return `初始画像已提交、已发布或本人已过目，${subject}仍可补充能力；尚未发布的改动需要完成审核才会生效。`
  }
}
