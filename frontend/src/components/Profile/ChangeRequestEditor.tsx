import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { isAxiosError } from "axios"
import { useEffect, useRef, useState } from "react"
import {
  type ChangeRequestPublic,
  EmployeeRequestsService,
  type ItemInput,
  type ProfileItem,
  type RequestPreview,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { levelLabel, record } from "./fields"

export type EditorSelection = {
  request?: ChangeRequestPublic
  item?: ProfileItem
  retract?: boolean
  preview?: boolean
  capabilityKey?: string
  objection?: boolean
}

export function ChangeRequestEditor({
  orgId,
  selection,
  profileItems,
  onClose,
}: {
  orgId: string
  selection: EditorSelection
  profileItems: ProfileItem[]
  onClose: () => void
}) {
  const client = useQueryClient()
  const path = { org_id: orgId }
  const [requestId, setRequestId] = useState(selection.request?.id)
  const [items, setItems] = useState<ItemInput[]>(() =>
    selection.request
      ? (selection.request.items ?? []).map((item) => ({
          op: item.op,
          capability_key: item.capability.key,
          statement: item.after?.statement ?? "撤回这条判断",
          level: item.after?.level,
          owner_statement: item.owner_statement,
          objection: item.objection,
          uncertainty: item.uncertainty,
          evidence_ids: (item.evidence ?? []).map((e) => e.evidence_id),
        }))
      : selection.item
        ? [
            {
              op: selection.retract
                ? "retract"
                : selection.item.claim
                  ? "modify"
                  : "add",
              capability_key: selection.item.capability.key,
              statement: selection.item.claim?.statement ?? "",
              level: selection.retract ? null : selection.item.claim?.level,
              evidence_ids: (selection.item.claim?.evidence ?? []).map(
                (e) => e.evidence_id,
              ),
            },
          ]
        : [],
  )
  const [preview, setPreview] = useState<RequestPreview | null>(null)
  const [showPreview, setShowPreview] = useState(!!selection.preview)
  const [notice, setNotice] = useState("")
  const [evidenceSkip, setEvidenceSkip] = useState(0)
  const activeField = useRef<HTMLTextAreaElement>(null)
  const dictionary = useQuery({
    queryKey: ["my-capabilities", orgId],
    queryFn: async () =>
      (await EmployeeRequestsService.readMyCapabilities({ path })).data,
  })
  const evidence = useQuery({
    queryKey: ["my-evidence", orgId, evidenceSkip],
    queryFn: async () =>
      (
        await EmployeeRequestsService.readMyEvidence({
          path,
          query: { skip: evidenceSkip, limit: 100 },
        })
      ).data,
  })
  useEffect(() => {
    if (dictionary.isSuccess && evidence.isSuccess) activeField.current?.focus()
  }, [dictionary.isSuccess, evidence.isSuccess])
  const initialPreview = useQuery({
    queryKey: ["request-preview", orgId, requestId],
    queryFn: async () =>
      (
        await EmployeeRequestsService.previewRequest({
          path: { ...path, request_id: requestId! },
        })
      ).data,
    enabled: !!selection.preview && !!requestId,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const displayedPreview = preview ?? (showPreview ? initialPreview.data : null)
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["change-requests", orgId] })
    client.invalidateQueries({ queryKey: ["profile", orgId] })
    client.invalidateQueries({ queryKey: ["profile-versions", orgId] })
    client.invalidateQueries({ queryKey: ["inbox", orgId] })
  }
  const save = useMutation({
    mutationFn: async (andPreview: boolean) => {
      const result = requestId
        ? await EmployeeRequestsService.updateItems({
            path: { ...path, request_id: requestId },
            body: { items },
          })
        : await EmployeeRequestsService.createDraft({ path, body: { items } })
      setRequestId(result.data.id)
      if (andPreview) {
        const resultPreview = await EmployeeRequestsService.previewRequest({
          path: { ...path, request_id: result.data.id },
        })
        setPreview(resultPreview.data)
        setShowPreview(true)
      } else setNotice("草稿已保存")
      refresh()
    },
  })
  const submit = useMutation({
    mutationFn: async () => {
      if (!displayedPreview) return
      try {
        await EmployeeRequestsService.submitRequest({
          path: { ...path, request_id: displayedPreview.request_id },
          body: { revision: displayedPreview.revision },
        })
        refresh()
        onClose()
      } catch (error) {
        if (isAxiosError(error) && error.response?.status === 409) {
          setNotice("内容已变化，请重新预览")
          setPreview(null)
          setShowPreview(false)
          refresh()
        }
        throw error
      }
    },
  })
  const reloadPreview = useMutation({
    mutationFn: async () => {
      const result = await EmployeeRequestsService.previewRequest({
        path: { ...path, request_id: requestId! },
      })
      setPreview(result.data)
      setItems(
        result.data.items.map(({ after: item }) => ({
          op: item.op,
          capability_key: item.capability.key,
          statement: item.after?.statement ?? "撤回这条判断",
          level: item.after?.level,
          owner_statement: item.owner_statement,
          objection: item.objection,
          uncertainty: item.uncertainty,
          evidence_ids: (item.evidence ?? []).map((e) => e.evidence_id),
        })),
      )
      setShowPreview(true)
      setNotice("")
      submit.reset()
    },
  })
  const update = (index: number, changes: Partial<ItemInput>) => {
    setItems(
      items.map((item, i) => (i === index ? { ...item, ...changes } : item)),
    )
    setNotice("")
  }
  const error =
    save.error ??
    submit.error ??
    reloadPreview.error ??
    dictionary.error ??
    evidence.error ??
    initialPreview.error
  const busy = save.isPending || submit.isPending || reloadPreview.isPending
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent
        className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl"
        showCloseButton={!busy}
      >
        <DialogTitle>{showPreview ? "提交前预览" : "更新我的画像"}</DialogTitle>
        <DialogDescription>
          画像不用于绩效、薪酬和晋升。提交后按企业审批规则处理；待审批的改动可以在批准前撤回。
        </DialogDescription>
        <SetupError error={error} />
        {notice && <p role="status">{notice}</p>}
        {notice === "内容已变化，请重新预览" ? (
          <Button disabled={busy} onClick={() => reloadPreview.mutate()}>
            重新预览最新内容
          </Button>
        ) : showPreview ? (
          <>
            {!displayedPreview && <p role="status">正在读取预览…</p>}
            {displayedPreview?.items.map(({ before, after, warning }) => (
              <section
                key={after.id}
                className="space-y-3 rounded-lg border p-4"
              >
                <h3 className="font-semibold">{after.capability.name}</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-sm text-muted-foreground">修改前</p>
                    <p>{before?.statement ?? "暂无判断"}</p>
                    <p>
                      等级：
                      {before?.level
                        ? levelLabel(
                            after.capability.level_rubric,
                            before.level,
                          )
                        : "未给等级"}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">修改后</p>
                    <p>
                      {after.op === "retract"
                        ? "撤回这条判断"
                        : after.after?.statement}
                    </p>
                    <p>
                      等级：
                      {after.after?.level
                        ? levelLabel(
                            after.capability.level_rubric,
                            after.after.level,
                          )
                        : "未给等级"}
                    </p>
                  </div>
                </div>
                {after.owner_statement && (
                  <p>依据说明：{after.owner_statement}</p>
                )}
                {after.uncertainty && <p>不确定之处：{after.uncertainty}</p>}
                {after.objection && <p>异议原因：{after.objection}</p>}
                <p className="text-sm">
                  相关证据：
                  {(after.evidence ?? []).map((e) => e.title).join("、") ||
                    "暂无"}
                </p>
                {warning && <p role="alert">等级需要重新选择：{warning}</p>}
              </section>
            ))}
            <div className="flex flex-wrap gap-3">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setShowPreview(false)
                  submit.reset()
                }}
              >
                返回修改
              </Button>
              <Button
                disabled={
                  busy ||
                  !displayedPreview?.items.length ||
                  displayedPreview.items.some((i) => i.warning)
                }
                onClick={() => submit.mutate()}
              >
                确认提交
              </Button>
            </div>
          </>
        ) : (
          <form
            className="space-y-5"
            onSubmit={(event) => {
              event.preventDefault()
              save.mutate(true)
            }}
          >
            <fieldset
              disabled={busy || !dictionary.data || !evidence.data}
              className="space-y-5"
            >
              {items.map((item, index) => {
                const capability = dictionary.data?.data.find(
                  (c) => c.key === item.capability_key,
                )
                const rubric = capability?.level_rubric
                const levels =
                  record(rubric) && Array.isArray(rubric.levels)
                    ? rubric.levels
                        .filter(record)
                        .filter((l) => typeof l.key === "string")
                    : []
                const invalidLevel =
                  item.level && !levels.some((l) => l.key === item.level)
                return (
                  <fieldset
                    key={item.capability_key}
                    className="space-y-3 rounded-lg border bg-muted/10 p-4"
                  >
                    <legend className="px-1 font-semibold">
                      {capability?.name ?? "能力已移出当前词典"}
                    </legend>
                    {item.op === "retract" ? (
                      <p>撤回这条判断</p>
                    ) : (
                      <>
                        <label className="block space-y-1">
                          陈述
                          <textarea
                            ref={
                              item.capability_key === selection.capabilityKey &&
                              !selection.objection
                                ? activeField
                                : undefined
                            }
                            required
                            value={item.statement}
                            className="block min-h-24 w-full rounded-md border bg-background p-2"
                            onChange={(e) =>
                              update(index, { statement: e.target.value })
                            }
                          />
                        </label>
                        <label className="block space-y-1">
                          等级
                          <select
                            value={item.level ?? ""}
                            className="block w-full rounded-md border bg-background p-2"
                            onChange={(e) =>
                              update(index, { level: e.target.value || null })
                            }
                          >
                            <option value="">未给等级</option>
                            {invalidLevel && (
                              <option value={item.level!}>
                                等级需要重新选择
                              </option>
                            )}
                            {levels.map((level) => (
                              <option
                                key={String(level.key)}
                                value={String(level.key)}
                              >
                                {String(level.label ?? level.key)}
                              </option>
                            ))}
                          </select>
                        </label>
                      </>
                    )}
                    <label className="block space-y-1">
                      依据说明
                      <textarea
                        value={item.owner_statement ?? ""}
                        className="block min-h-20 w-full rounded-md border bg-background p-2"
                        onChange={(e) =>
                          update(index, {
                            owner_statement: e.target.value || null,
                          })
                        }
                      />
                    </label>
                    {selection.request &&
                      ["initial_sync", "import"].includes(
                        selection.request.origin,
                      ) && (
                        <label className="block space-y-1">
                          异议原因
                          <textarea
                            ref={
                              item.capability_key === selection.capabilityKey &&
                              selection.objection
                                ? activeField
                                : undefined
                            }
                            value={item.objection ?? ""}
                            className="block min-h-20 w-full rounded-md border bg-background p-2"
                            onChange={(e) =>
                              update(index, {
                                objection: e.target.value || null,
                              })
                            }
                          />
                        </label>
                      )}
                    <details>
                      <summary className="cursor-pointer">
                        选择相关证据（已选 {item.evidence_ids?.length ?? 0}）
                      </summary>
                      <div className="max-h-48 space-y-2 overflow-y-auto py-3">
                        {evidence.data?.data.map((entry) => (
                          <label
                            key={entry.id}
                            className="flex items-start gap-2 text-sm"
                          >
                            <input
                              type="checkbox"
                              checked={
                                item.evidence_ids?.includes(entry.id) ?? false
                              }
                              onChange={(e) =>
                                update(index, {
                                  evidence_ids: e.target.checked
                                    ? [...(item.evidence_ids ?? []), entry.id]
                                    : item.evidence_ids?.filter(
                                        (id) => id !== entry.id,
                                      ),
                                })
                              }
                            />
                            {entry.title}
                          </label>
                        ))}
                      </div>
                      <div className="flex gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          disabled={!evidenceSkip}
                          onClick={() => setEvidenceSkip(evidenceSkip - 100)}
                        >
                          上一页证据
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={
                            evidenceSkip + 100 >= (evidence.data?.count ?? 0)
                          }
                          onClick={() => setEvidenceSkip(evidenceSkip + 100)}
                        >
                          下一页证据
                        </Button>
                      </div>
                    </details>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setItems(items.filter((_, i) => i !== index))
                      }
                    >
                      移除此项改动
                    </Button>
                  </fieldset>
                )
              })}
              <label className="block space-y-1">
                新增能力
                <select
                  value=""
                  className="block w-full rounded-md border bg-background p-2"
                  onChange={(e) => {
                    if (e.target.value)
                      setItems([
                        ...items,
                        {
                          capability_key: e.target.value,
                          op: "add",
                          statement: "",
                          evidence_ids: [],
                        },
                      ])
                  }}
                >
                  <option value="">从当前词典选择</option>
                  {dictionary.data?.data
                    .filter(
                      (c) =>
                        !items.some((i) => i.capability_key === c.key) &&
                        !profileItems.some(
                          (i) => i.capability.key === c.key && i.claim,
                        ),
                    )
                    .map((c) => (
                      <option key={c.id} value={c.key}>
                        {c.name}
                      </option>
                    ))}
                </select>
              </label>
              <p className="text-sm text-muted-foreground">
                依据说明和异议仅本人及管辖经理可读。
              </p>
              <div className="flex flex-wrap gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => save.mutate(false)}
                >
                  保存草稿
                </Button>
                <Button
                  type="submit"
                  disabled={
                    !items.length ||
                    items.some(
                      (i) =>
                        i.level &&
                        !dictionary.data?.data.find(
                          (c) => c.key === i.capability_key,
                        ),
                    )
                  }
                >
                  预览并提交
                </Button>
              </div>
            </fieldset>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
