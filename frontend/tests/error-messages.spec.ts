import { expect, test } from "@playwright/test"
import { AxiosError, type AxiosResponse } from "axios"
import { handleError } from "../src/utils"

test("API errors show Chinese validation, detail and fallback messages", () => {
  for (const [status, detail, expected] of [
    [400, "当前密码不正确", "当前密码不正确"],
    [409, { code: "stale", message: "请重新预览" }, "请重新预览"],
    [422, [{ msg: "Field required" }], "填写内容格式有误，请检查后重试"],
    [429, null, "操作过于频繁，请稍后重试"],
    [500, null, "服务暂时异常，请稍后重试"],
    [503, null, "服务暂时不可用，请稍后重试"],
  ] as const) {
    const response = { status, data: { detail } } as AxiosResponse
    let message = ""
    handleError.call(
      (value) => {
        message = value
      },
      new AxiosError(
        "Request failed",
        undefined,
        undefined,
        undefined,
        response,
      ),
    )
    expect(message).toBe(expected)
  }
  let message = ""
  handleError.call((value) => {
    message = value
  }, new AxiosError("Network Error"))
  expect(message).toBe("无法连接服务器，请检查网络")
})
