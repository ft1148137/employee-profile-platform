import { Link } from "@tanstack/react-router"
import { Button } from "@/components/ui/button"

const ErrorComponent = () => {
  return (
    <div
      className="flex min-h-screen items-center justify-center flex-col p-4"
      data-testid="error-component"
    >
      <title>页面出错 - 员工画像平台</title>
      <div className="flex items-center z-10">
        <div className="flex flex-col ml-4 items-center justify-center p-4">
          <span className="text-6xl md:text-8xl font-bold leading-none mb-4">
            出错了
          </span>
          <span className="text-2xl font-bold mb-2">页面暂时无法显示</span>
        </div>
      </div>

      <p className="text-lg text-muted-foreground mb-4 text-center z-10">
        发生了一点问题，请稍后重试。
      </p>
      <Link to="/">
        <Button>返回首页</Button>
      </Link>
    </div>
  )
}

export default ErrorComponent
