import { AxiosError } from "axios"

export function redirectToPasswordChange(err: unknown): boolean {
  if (
    err instanceof AxiosError &&
    err.response?.status === 403 &&
    err.response.data?.detail?.code === "password_change_required"
  ) {
    if (window.location.pathname !== "/change-password") {
      window.location.href = "/change-password"
    }
    return true
  }
  return false
}

function extractErrorMessage(err: Error): string {
  if (err instanceof AxiosError) {
    const errDetail = (err.response?.data as any)?.detail
    if (Array.isArray(errDetail) && errDetail.length > 0) {
      return errDetail[0].msg
    }
    if (typeof errDetail === "string") {
      return errDetail
    }
    if (typeof errDetail?.message === "string") {
      return errDetail.message
    }
    return err.message
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
