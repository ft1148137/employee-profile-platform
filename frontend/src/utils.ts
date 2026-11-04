import { AxiosError } from "axios"

export function safeNext(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    Array.from(value).some((char) => char.charCodeAt(0) <= 32)
  )
    return "/"
  const url = new URL(value, "https://platform.invalid")
  if (
    url.origin !== "https://platform.invalid" ||
    url.pathname.startsWith("//")
  )
    return "/"
  return url.pathname + url.search + url.hash
}

export function redirectToPasswordChange(err: unknown): boolean {
  if (
    err instanceof AxiosError &&
    err.response?.status === 403 &&
    err.response.data?.detail?.code === "password_change_required"
  ) {
    if (window.location.pathname !== "/change-password") {
      window.location.href = `/change-password${window.location.pathname === "/oauth/consent" ? `?next=${encodeURIComponent(window.location.pathname + window.location.search)}` : ""}`
    }
    return true
  }
  return false
}

function extractErrorMessage(err: Error): string {
  if (err instanceof AxiosError) {
    if (!err.response) return "无法连接服务器，请检查网络"
    if (err.response.status === 422) return "提交的内容格式不正确"
    const detail: unknown = err.response.data?.detail
    if (typeof detail === "string") return detail
    if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof detail.message === "string"
    )
      return detail.message
    const messages: Record<number, string> = {
      400: "提交的内容有误，请检查后重试",
      401: "登录已失效，请重新登录",
      403: "你没有权限执行此操作",
      404: "请求的内容不存在或你没有权限查看",
      409: "内容已发生变化，请刷新后重试",
      429: "操作过于频繁，请稍后重试",
      500: "服务器暂时出错，请稍后重试",
      502: "服务器暂时不可用，请稍后重试",
      503: "服务暂时不可用，请稍后重试",
    }
    return messages[err.response.status] ?? "操作失败，请稍后重试"
  }
  return "操作失败，请稍后重试"
}

export const handleError = function (this: (msg: string) => void, err: Error) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
