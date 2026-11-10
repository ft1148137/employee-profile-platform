import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import { ModelSettingService, type ModelSettingUpdateWritable } from "@/client"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ChatGptConnect } from "./ChatGptConnect"

const providers = [
  ["openai", "OpenAI"],
  ["anthropic", "Anthropic"],
  ["deepseek", "DeepSeek"],
  ["qwen", "通义千问"],
  ["moonshotai", "Kimi 国际"],
  ["moonshotai-cn", "Kimi 中国"],
  ["kimi-coding", "Kimi Coding"],
  ["openrouter", "OpenRouter"],
] as const
const qwenUrls = [
  "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
  "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
]

export function ModelSettingForm({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const [changes, setChanges] = useState<ModelSettingUpdateWritable | null>(
    null,
  )
  const [apiKey, setApiKey] = useState("")
  const [clearKey, setClearKey] = useState(false)
  const settings = useQuery({
    queryKey: ["model-setting", orgId],
    queryFn: async () =>
      (await ModelSettingService.settingGetModelSetting({ path })).data,
  })
  const save = useMutation({
    mutationFn: (body: ModelSettingUpdateWritable) =>
      ModelSettingService.settingPutModelSetting({ path, body }),
    onSuccess: ({ data }) => {
      client.setQueryData(["model-setting", orgId], data)
      client.invalidateQueries({ queryKey: ["setup", orgId] })
      setChanges(null)
      setApiKey("")
      setClearKey(false)
    },
  })
  const test = useMutation({
    mutationFn: () => ModelSettingService.settingTestModelSetting({ path }),
  })
  const resetManual = useCallback(() => {
    setChanges(null)
    setApiKey("")
    setClearKey(false)
  }, [])
  const pageDraft = usePageDraft(orgId, "update_model_setting", resetManual)
  if (settings.isPending) return <p role="status">正在读取模型设置…</p>
  if (settings.isError)
    return (
      <>
        <SetupError error={settings.error} />
        <Button onClick={() => settings.refetch()}>重试</Button>
      </>
    )
  const saved = settings.data
  const current: ModelSettingUpdateWritable = pageDraft.values(
    changes ?? {
      enabled: saved.enabled ?? false,
      kind: saved.kind as ModelSettingUpdateWritable["kind"],
      provider: saved.provider,
      model: saved.model,
      base_url: saved.base_url,
      reasoning_effort: saved.reasoning_effort,
    },
  )
  const chatgpt = current.kind === "chatgpt_subscription"
  const changedDestination =
    saved.has_api_key &&
    !chatgpt &&
    (current.kind !== saved.kind ||
      current.provider !== saved.provider ||
      (current.base_url ?? "").replace(/\/$/, "") !==
        (saved.base_url ?? "").replace(/\/$/, ""))
  function change(values: Partial<ModelSettingUpdateWritable>) {
    setChanges({ ...current, ...values })
    save.reset()
    test.reset()
  }
  const selectClass = "h-10 w-full rounded-md border bg-background px-3"
  return (
    <>
      <PageDraft
        orgId={orgId}
        draft={pageDraft.draft}
        error={pageDraft.error}
      />
      <form
        className="max-w-2xl space-y-5"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate({
            ...current,
            api_key: chatgpt ? null : clearKey ? "" : apiKey || null,
          })
        }}
      >
        <fieldset
          className="space-y-5"
          disabled={pageDraft.blocked || save.isPending}
        >
          <DraftField changed={pageDraft.changed("enabled")}>
            <label className="flex gap-3 items-center">
              <input
                type="checkbox"
                checked={current.enabled}
                onChange={(e) => change({ enabled: e.target.checked })}
              />
              启用模型
            </label>
          </DraftField>
          <p className="text-sm text-muted-foreground">
            打开后可根据工作记录起草画像并使用机器人；关闭后仍可在网页上填写或导入画像文件。
          </p>
          {saved.kind &&
            saved.kind !== "chatgpt_subscription" &&
            !saved.provider && <p role="status">请重新选择提供方</p>}
          <DraftField changed={pageDraft.changed("kind")}>
            <label className="block space-y-2">
              <span>模型类型</span>
              <select
                className={selectClass}
                value={current.kind ?? ""}
                required={current.enabled}
                onChange={(e) => {
                  const kind = e.target
                    .value as ModelSettingUpdateWritable["kind"]
                  change({
                    kind,
                    provider:
                      kind === "local"
                        ? "ollama"
                        : kind === "openai_compatible"
                          ? "openai-compatible"
                          : null,
                    model:
                      kind === "chatgpt_subscription"
                        ? "chatgpt/gpt-6.1-sol"
                        : "",
                    base_url: null,
                    reasoning_effort:
                      kind === "chatgpt_subscription" ? "high" : null,
                  })
                  setApiKey("")
                  setClearKey(false)
                }}
              >
                <option value="">请选择类型</option>
                <option value="cloud_api">云端模型服务</option>
                <option value="local">本地模型</option>
                <option value="openai_compatible">其他兼容模型服务</option>
                <option value="chatgpt_subscription">ChatGPT 订阅</option>
              </select>
            </label>
          </DraftField>
          <p className="text-sm text-muted-foreground">
            选择企业使用的模型服务；云端服务按厂商规则计费，订阅方式共用所连接账号的额度。
          </p>
          {current.kind && (
            <>
              <DraftField changed={pageDraft.changed("model")}>
                <label htmlFor="model-name" className="block space-y-2">
                  <span>模型名</span>
                  <Input
                    id="model-name"
                    required={current.enabled}
                    value={current.model ?? ""}
                    placeholder={
                      chatgpt ? "chatgpt/gpt-6.1-sol" : "填写厂商提供的模型名称"
                    }
                    onChange={(e) => change({ model: e.target.value })}
                  />
                </label>
              </DraftField>
              <p className="text-sm text-muted-foreground">
                填写模型服务中实际可用的名称，保存后可用「测试连接」确认。
              </p>
              {chatgpt && (
                <p className="text-sm text-muted-foreground">
                  模型名保留 chatgpt/
                  前缀；能使用哪些模型，以所连接账号的订阅为准。
                </p>
              )}
              <details className="space-y-3 rounded-lg border p-4">
                <summary className="cursor-pointer font-medium">
                  高级设置：模型思考深度
                </summary>
                <DraftField changed={pageDraft.changed("reasoning_effort")}>
                  <label className="block space-y-2">
                    <span>推理强度</span>
                    <select
                      className={selectClass}
                      value={current.reasoning_effort ?? "off"}
                      onChange={(e) =>
                        change({
                          reasoning_effort: e.target
                            .value as ModelSettingUpdateWritable["reasoning_effort"],
                        })
                      }
                    >
                      {(
                        [
                          "off",
                          "minimal",
                          "low",
                          "medium",
                          "high",
                          "xhigh",
                        ] as const
                      ).map((v, i) => (
                        <option key={v} value={v}>
                          {["默认", "极少", "较少", "适中", "较深", "最深"][i]}
                        </option>
                      ))}
                    </select>
                  </label>
                </DraftField>
                <p className="text-sm text-muted-foreground">
                  思考越深通常等待越久、消耗额度越多；保留默认值即可，不同模型支持的深度可能不同。
                </p>
              </details>
              {!chatgpt && (
                <>
                  {current.kind === "cloud_api" && (
                    <>
                      <DraftField changed={pageDraft.changed("provider")}>
                        <label className="block space-y-2">
                          <span>提供方</span>
                          <select
                            aria-label="提供方"
                            className={selectClass}
                            required={current.enabled}
                            value={current.provider ?? ""}
                            onChange={(e) => {
                              const provider = e.target
                                .value as ModelSettingUpdateWritable["provider"]
                              change({
                                provider,
                                base_url:
                                  provider === "qwen" ? qwenUrls[0] : null,
                              })
                              setApiKey("")
                            }}
                          >
                            <option value="">请重新选择提供方</option>
                            {providers.map(([value, label]) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                      </DraftField>
                      <p className="text-sm text-muted-foreground">
                        选择实际提供模型服务的厂商，密钥也必须由同一厂商提供。
                      </p>
                      <p className="text-sm">
                        Claude 需要 Anthropic API
                        key（由模型厂商提供的连接密钥），不支持用 Claude
                        订阅登录。
                      </p>
                      {current.provider === "qwen" && (
                        <DraftField changed={pageDraft.changed("base_url")}>
                          <label className="block space-y-2">
                            <span>百炼区域</span>
                            <select
                              className={selectClass}
                              value={current.base_url ?? qwenUrls[0]}
                              onChange={(e) =>
                                change({ base_url: e.target.value })
                              }
                            >
                              {qwenUrls.map((url, i) => (
                                <option key={url} value={url}>
                                  {["中国内地", "新加坡", "美国"][i]}
                                </option>
                              ))}
                            </select>
                          </label>
                        </DraftField>
                      )}
                    </>
                  )}
                  {current.kind !== "cloud_api" && (
                    <DraftField changed={pageDraft.changed("base_url")}>
                      <label htmlFor="model-url" className="block space-y-2">
                        <span>
                          base URL
                          {current.kind === "openai_compatible"
                            ? "（填到 /v1）"
                            : "（如 http://主机:11434）"}
                        </span>
                        <Input
                          id="model-url"
                          type="url"
                          required={current.enabled}
                          value={current.base_url ?? ""}
                          onChange={(e) =>
                            change({ base_url: e.target.value || null })
                          }
                        />
                      </label>
                    </DraftField>
                  )}
                  {current.kind !== "cloud_api" && (
                    <p className="text-sm text-muted-foreground">
                      base URL
                      是模型服务的连接地址，请向服务提供方索取；它不是聊天网页的网址。
                    </p>
                  )}
                  <label htmlFor="model-key" className="block space-y-2">
                    <span>
                      密钥{current.kind !== "cloud_api" && "（可选）"}
                    </span>
                    <Input
                      id="model-key"
                      type="password"
                      autoComplete="new-password"
                      required={
                        !clearKey &&
                        (changedDestination ||
                          (current.enabled &&
                            current.kind === "cloud_api" &&
                            !saved.has_api_key))
                      }
                      value={apiKey}
                      placeholder={
                        saved.has_api_key && !clearKey
                          ? "已保存，留空表示不修改"
                          : "填写密钥"
                      }
                      onChange={(e) => {
                        setApiKey(e.target.value)
                        save.reset()
                      }}
                    />
                  </label>
                  <p className="text-sm text-muted-foreground">
                    密钥用于连接模型服务，加密保存；已保存时留空即可保留。
                  </p>
                  {changedDestination && <p role="status">需要重新填写密钥</p>}
                  {saved.has_api_key && (
                    <div className="space-y-2">
                      <label className="flex gap-2">
                        <input
                          type="checkbox"
                          checked={clearKey}
                          onChange={(e) => setClearKey(e.target.checked)}
                        />
                        清除密钥
                      </label>
                      <p className="text-sm text-muted-foreground">
                        勾选并保存后删除已保存的密钥，需要重新填写才能连接。
                      </p>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          <p className="text-sm text-muted-foreground">
            发给模型的内容：能力词典，以及工作记录的标题、类型、状态、标签、项目、日期和说明；不发姓名、工号、邮箱和外部账号。
          </p>
          <SetupError error={save.error ?? test.error} />
          {save.isSuccess && <p role="status">模型设置已保存。</p>}
          {test.data && (
            <p role={test.data.data.ok ? "status" : "alert"}>
              {test.data.data.message}
            </p>
          )}
          <div className="flex gap-3">
            <Button type="submit" disabled={save.isPending}>
              保存
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                save.isPending ||
                test.isPending ||
                !!changes ||
                !!apiKey ||
                clearKey ||
                !saved.enabled
              }
              onClick={() => test.mutate()}
            >
              测试连接
            </Button>
          </div>
        </fieldset>
        {chatgpt && <ChatGptConnect orgId={orgId} />}
      </form>
    </>
  )
}
