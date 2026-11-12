import { Link } from "@tanstack/react-router"
import { Button } from "@/components/ui/button"

export function RosterStep() {
  return (
    <div className="space-y-4">
      <p>在管理助手里上传名单，任意格式都可以。核对人员变动，确认后生效。</p>
      <Button asChild>
        <Link
          to="/admin/assistant"
          search={{ prompt: "帮我按这份名单导入员工" }}
        >
          前往管理助手
        </Link>
      </Button>
    </div>
  )
}
