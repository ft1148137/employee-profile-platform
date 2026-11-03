import type {
  EvidenceQuality,
  FillStatus,
  InferenceStatus,
  ProgressReviewStatus,
} from "@/client"

const neutral = "bg-muted text-muted-foreground"
const positive = "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
const pending = "bg-amber-500/10 text-amber-700 dark:text-amber-300"
const active = "bg-sky-500/10 text-sky-700 dark:text-sky-300"
const negative = "bg-rose-500/10 text-rose-700 dark:text-rose-300"
type Label = { label: string; className: string }

export const labels = {
  assessment_status: {
    confirmed: { label: "已确认", className: positive },
    tentative: { label: "暂定", className: pending },
    unknown: { label: "未知", className: neutral },
    disputed: { label: "存在争议", className: negative },
  } satisfies Record<string, Label>,
  fill_status: {
    not_started: { label: "未开始", className: neutral },
    in_progress: { label: "进行中", className: active },
    submitted: { label: "已提交", className: positive },
  } satisfies Record<FillStatus, Label>,
  inference_status: {
    pending: { label: "待处理", className: neutral },
    running: { label: "处理中", className: active },
    generated: { label: "已生成候选", className: positive },
    failed: { label: "失败可重试", className: negative },
  } satisfies Record<InferenceStatus, Label>,
  review_status: {
    none: { label: "未进入审核", className: neutral },
    pending_employee: { label: "待本人确认", className: pending },
    pending_manager: { label: "待经理审核", className: active },
    published: { label: "已发布", className: positive },
    disputed: { label: "存在争议", className: negative },
  } satisfies Record<ProgressReviewStatus, Label>,
  evidence_quality: {
    sufficient: { label: "覆盖充分", className: positive },
    weak: { label: "证据薄弱", className: pending },
    conflicting: { label: "来源冲突", className: negative },
    unknown: { label: "未知", className: neutral },
  } satisfies Record<EvidenceQuality, Label>,
  confidence: {
    low: { label: "低", className: neutral },
    medium: { label: "中", className: pending },
    high: { label: "高", className: active },
  } satisfies Record<string, Label>,
  review_answer: {
    needs_support: { label: "需要协助", className: pending },
    routine: { label: "能完成常规任务", className: active },
    independent_complex: { label: "能独立完成复杂任务", className: positive },
    not_sure: { label: "不确定", className: neutral },
  } satisfies Record<string, Label>,
  evidence_kind: {
    work_record: { label: "Jira 记录", className: active },
    self_report: { label: "员工自述", className: neutral },
    manager_review: { label: "经理评价", className: pending },
  } satisfies Record<string, Label>,
}
