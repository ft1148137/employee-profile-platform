import { useMutation, useQuery } from "@tanstack/react-query"
import { useId, useRef, useState } from "react"
import {
  CapabilitiesService,
  type DictionaryDefinition,
  type DictionaryPreview,
} from "@/client"
import { DraftField } from "@/components/AdminAssistant/DraftField"
import type { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { DictionaryPreviewPanel } from "./DictionaryPreviewPanel"
import { SetupError } from "./SetupError"

type Props = {
  orgId: string
  onClose: () => void
  onSaved: () => void
  suggestion?: ReturnType<typeof usePageDraft>
}

export function DictionaryEditor(props: Props) {
  const current = useQuery({
    queryKey: ["dictionary-editor", props.orgId],
    queryFn: async () =>
      (
        await CapabilitiesService.readDictionaryEditor({
          path: { org_id: props.orgId },
        })
      ).data,
    refetchOnWindowFocus: false,
    gcTime: 0,
  })
  if (current.isPending) return <p role="status">正在加载编辑内容…</p>
  if (current.isError) return <SetupError error={current.error} />
  return <Editor {...props} initial={current.data} />
}

function Editor({
  orgId,
  onClose,
  onSaved,
  initial,
  suggestion,
}: Props & { initial: DictionaryPreview }) {
  const prefix = useId()
  const nextId = useRef(initial.definitions.length)
  const [baseVersion] = useState(initial.expected_version)
  const [rows, setRows] = useState(() =>
    [...initial.definitions]
      .sort((a, b) => a.category.localeCompare(b.category, "zh-CN"))
      .map((definition, index) => ({
        id: index,
        locked: true,
        definition,
      })),
  )
  const [preview, setPreview] = useState<DictionaryPreview | null>(null)
  const [redraft, setRedraft] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const body = {
    definitions: rows.map((r) => r.definition),
    expected_version: baseVersion,
    redraft,
  }
  const check = useMutation({
    mutationFn: () =>
      CapabilitiesService.previewDictionaryEditor({
        path: { org_id: orgId },
        body,
      }),
    onMutate: () => {
      setError(null)
      setPreview(null)
    },
    onSuccess: ({ data }) => setPreview(data),
    onError: setError,
  })
  const save = useMutation({
    mutationFn: () =>
      CapabilitiesService.updateDictionary({ path: { org_id: orgId }, body }),
    onMutate: () => setError(null),
    onSuccess: onSaved,
    onError: setError,
  })
  const busy = check.isPending || save.isPending || !!suggestion?.blocked
  const proposed = suggestion?.item
    ? suggestion.values(initial).definitions
    : null
  const baseline = { ...initial, ...suggestion?.item?.before }.definitions
  const visibleRows = proposed
    ? [
        ...proposed.map((definition, id) => ({
          id,
          locked: true,
          definition,
          removed: false,
        })),
        ...baseline
          .filter((d) => !proposed.some((next) => next.key === d.key))
          .map((definition, index) => ({
            id: proposed.length + index,
            locked: true,
            definition,
            removed: true,
          })),
      ]
    : rows.map((row) => ({ ...row, removed: false }))
  function update(id: number, change: Partial<DictionaryDefinition>) {
    setPreview(null)
    setRows((values) =>
      values.map((row) =>
        row.id === id
          ? { ...row, definition: { ...row.definition, ...change } }
          : row,
      ),
    )
  }
  function add() {
    const used = new Set(
      [...initial.definitions, ...body.definitions].map((d) => d.key),
    )
    let sequence = 1
    while (used.has(`capability_${sequence}`)) sequence++
    setRows([
      ...rows,
      {
        id: nextId.current++,
        locked: false,
        definition: {
          key: `capability_${sequence}`,
          name: "",
          category: "未分类",
          description: "",
          ai_inference: true,
          level_rubric: {
            levels: [{ key: "basic", label: "基础", description: "" }],
          },
        },
      },
    ])
    setPreview(null)
  }
  return (
    <section
      aria-label="网页编辑能力词典"
      className="space-y-6 rounded-xl border p-4 sm:p-6"
    >
      <header>
        <h3 className="text-xl font-semibold">编辑能力与等级</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          基于第 {baseVersion}{" "}
          版。能力标识由系统生成，新能力保存前可以修改，保存后固定。编辑不会直接改变员工的判断。
        </p>
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (suggestion?.blocked) return
          check.mutate()
        }}
      >
        <fieldset disabled={busy} className="min-w-0 space-y-6">
          {visibleRows.map(
            ({ id, locked, definition: d, removed }, rowIndex) => (
              <div key={id} className="space-y-4">
                {(rowIndex === 0 ||
                  visibleRows[rowIndex - 1].definition.category !==
                    d.category) && (
                  <h4 className="border-l-2 border-primary pl-3 font-semibold">
                    {d.category || "未分类"}
                  </h4>
                )}
                <DraftField
                  changed={
                    !!proposed &&
                    (removed ||
                      JSON.stringify(
                        baseline.find((entry) => entry.key === d.key),
                      ) !== JSON.stringify(d))
                  }
                  label={
                    removed
                      ? "助手建议 · 删除"
                      : baseline.some((entry) => entry.key === d.key)
                        ? "助手建议 · 修改"
                        : "助手建议 · 新增"
                  }
                >
                  <article
                    aria-label={d.name || "新增能力"}
                    className="min-w-0 space-y-4 rounded-lg border bg-muted/10 p-4"
                  >
                    <div className="grid gap-4 sm:grid-cols-2">
                      {(
                        [
                          ["name", "能力名称", 200],
                          ["key", "能力标识", 100],
                          ["category", "分类", 200],
                          ["description", "能力说明", 4000],
                        ] as const
                      ).map(([field, label, maxLength]) => (
                        <label
                          key={field}
                          htmlFor={`${prefix}-${id}-${field}`}
                          className="space-y-2 text-sm"
                        >
                          {label}
                          <Input
                            id={`${prefix}-${id}-${field}`}
                            required={field === "name" || field === "key"}
                            maxLength={maxLength}
                            disabled={locked && field === "key"}
                            value={d[field]}
                            onChange={(e) =>
                              update(id, { [field]: e.target.value })
                            }
                            onBlur={
                              field === "category"
                                ? () =>
                                    setRows((values) =>
                                      [...values].sort((a, b) =>
                                        a.definition.category.localeCompare(
                                          b.definition.category,
                                          "zh-CN",
                                        ),
                                      ),
                                    )
                                : undefined
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <label
                      htmlFor={`${prefix}-${id}-ai`}
                      className="flex items-center gap-3 text-sm"
                    >
                      <Checkbox
                        id={`${prefix}-${id}-ai`}
                        checked={d.ai_inference === false}
                        onCheckedChange={(value) =>
                          update(id, { ai_inference: value !== true })
                        }
                      />
                      不由 AI 推断
                    </label>
                    <fieldset className="min-w-0 space-y-3">
                      <legend className="mb-2 text-sm font-medium">等级</legend>
                      {d.level_rubric.levels.map((level, index) => (
                        <div
                          key={index}
                          className="grid min-w-0 gap-3 rounded-md border p-3 sm:grid-cols-2"
                        >
                          {(
                            [
                              ["key", "等级标识", 100],
                              ["label", "等级名称", 200],
                              ["description", "等级描述", 4000],
                            ] as const
                          ).map(([field, label, maxLength]) => (
                            <label
                              key={field}
                              htmlFor={`${prefix}-${id}-${index}-${field}`}
                              className="space-y-2 text-sm"
                            >
                              {label}
                              <Input
                                id={`${prefix}-${id}-${index}-${field}`}
                                required
                                maxLength={maxLength}
                                value={level[field]}
                                onChange={(e) =>
                                  update(id, {
                                    level_rubric: {
                                      levels: d.level_rubric.levels.map(
                                        (l, i) =>
                                          i === index
                                            ? {
                                                ...l,
                                                [field]: e.target.value,
                                              }
                                            : l,
                                      ),
                                    },
                                  })
                                }
                              />
                            </label>
                          ))}
                          <Button
                            type="button"
                            variant="ghost"
                            disabled={d.level_rubric.levels.length === 1}
                            onClick={() =>
                              update(id, {
                                level_rubric: {
                                  levels: d.level_rubric.levels.filter(
                                    (_, i) => i !== index,
                                  ),
                                },
                              })
                            }
                          >
                            删除等级
                          </Button>
                        </div>
                      ))}
                      <Button
                        type="button"
                        variant="outline"
                        disabled={d.level_rubric.levels.length >= 20}
                        onClick={() =>
                          update(id, {
                            level_rubric: {
                              levels: [
                                ...d.level_rubric.levels,
                                { key: "", label: "", description: "" },
                              ],
                            },
                          })
                        }
                      >
                        新增等级
                      </Button>
                    </fieldset>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setRows(rows.filter((r) => r.id !== id))
                        setPreview(null)
                      }}
                    >
                      删除能力
                    </Button>
                  </article>
                </DraftField>
              </div>
            ),
          )}
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={rows.length >= 1000}
              onClick={add}
            >
              新增能力
            </Button>
            <Button type="submit" disabled={!rows.length}>
              预览改动
            </Button>
            <Button type="button" variant="ghost" onClick={onClose}>
              取消编辑
            </Button>
          </div>
        </fieldset>
      </form>
      <SetupError error={error} />
      {preview && !suggestion?.draft && (
        <DictionaryPreviewPanel
          preview={preview}
          busy={busy}
          redraft={redraft}
          onRedraft={setRedraft}
          onSave={() => save.mutate()}
        />
      )}
    </section>
  )
}
