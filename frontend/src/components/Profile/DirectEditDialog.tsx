import { useMutation, useQueryClient } from "@tanstack/react-query"
import { isAxiosError } from "axios"
import { useState } from "react"
import {
  type DirectEditInput,
  type DirectEditResult,
  type ProfileItem,
  ProfilesService,
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
import { ProfileVisibility } from "./ProfileVisibility"

export function DirectEditDialog({
  orgId,
  employeeId,
  item,
  revision,
  onClose,
}: {
  orgId: string
  employeeId: string
  item: ProfileItem
  revision: number | null
  onClose: () => void
}) {
  const client = useQueryClient()
  // Keep the version the editor opened with, even if the profile refetches.
  const [baseRevision] = useState(revision)
  const [statement, setStatement] = useState(item.claim?.statement ?? "")
  const [level, setLevel] = useState(item.claim?.level ?? "")
  const [retract, setRetract] = useState(false)
  const [preview, setPreview] = useState<DirectEditResult | null>(null)
  const path = { org_id: orgId, employee_id: employeeId }
  const body: DirectEditInput = {
    base_revision: baseRevision,
    items: [
      {
        capability_key: item.capability.key,
        op: retract ? "retract" : item.claim ? "modify" : "add",
        statement: retract ? "撤回这条判断" : statement.trim(),
        level: retract ? null : level || null,
      },
    ],
  }
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["profile", orgId, employeeId] }),
      client.invalidateQueries({
        queryKey: ["profile-versions", orgId, employeeId],
      }),
      client.invalidateQueries({
        queryKey: ["change-requests", orgId, employeeId],
      }),
      client.invalidateQueries({ queryKey: ["inbox", orgId] }),
    ])
  const prepare = useMutation({
    mutationFn: async () =>
      (await ProfilesService.previewDirectEdit({ path, body })).data,
    onSuccess: setPreview,
  })
  const publish = useMutation({
    mutationFn: async () => ProfilesService.directEdit({ path, body }),
    onSuccess: async () => {
      await refresh()
      onClose()
    },
  })
  const busy = prepare.isPending || publish.isPending
  const error = prepare.error ?? publish.error
  const conflict = isAxiosError(error) && error.response?.status === 409
  const rubric = item.capability.level_rubric
  const levels =
    record(rubric) && Array.isArray(rubric.levels)
      ? rubric.levels
          .filter(record)
          .filter((entry) => typeof entry.key === "string")
      : []
  const invalidLevel =
    level !== "" && !levels.some((entry) => entry.key === level)
  const change = preview?.items[0]
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent
        showCloseButton={!busy}
        className="max-h-[90dvh] overflow-y-auto"
      >
        <DialogTitle>直接修改「{item.capability.name}」</DialogTitle>
        <DialogDescription>
          直接发布，不需要本人同意，会通知本人。
        </DialogDescription>
        <ProfileVisibility />
        {conflict ? (
          <>
            <p role="alert">画像已变化，请刷新后再改</p>
            <Button
              onClick={async () => {
                await refresh()
                onClose()
              }}
            >
              刷新画像
            </Button>
          </>
        ) : (
          <>
            <SetupError error={error} />
            {change ? (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <section
                    aria-label="修改前"
                    className="space-y-2 rounded-lg border p-4"
                  >
                    <h3 className="font-semibold">修改前</h3>
                    <p>{change.before?.statement ?? "暂无判断"}</p>
                    <p>
                      等级：
                      {change.before?.level
                        ? levelLabel(rubric, change.before.level)
                        : "未给等级"}
                    </p>
                  </section>
                  <section
                    aria-label="修改后"
                    className="space-y-2 rounded-lg border p-4"
                  >
                    <h3 className="font-semibold">修改后</h3>
                    <p>{retract ? "撤回这条判断" : change.after.statement}</p>
                    <p>
                      等级：
                      {change.after.level
                        ? levelLabel(rubric, change.after.level)
                        : "未给等级"}
                    </p>
                    <p>状态：{retract ? "未知" : "已确认"}</p>
                  </section>
                </div>
                <div className="flex gap-3">
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      setPreview(null)
                      publish.reset()
                    }}
                  >
                    返回修改
                  </Button>
                  <Button disabled={busy} onClick={() => publish.mutate()}>
                    确认直接发布
                  </Button>
                </div>
              </>
            ) : (
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault()
                  prepare.mutate()
                }}
              >
                <fieldset disabled={busy} className="space-y-4">
                  {item.claim && (
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={retract}
                        onChange={(event) => setRetract(event.target.checked)}
                      />
                      撤回这条判断
                    </label>
                  )}
                  {!retract && (
                    <>
                      <label className="block space-y-1">
                        陈述
                        <textarea
                          required
                          value={statement}
                          className="block min-h-24 w-full rounded-md border bg-background p-2"
                          onChange={(event) => setStatement(event.target.value)}
                        />
                      </label>
                      <label className="block space-y-1">
                        等级
                        <select
                          value={level}
                          className="block w-full rounded-md border bg-background p-2"
                          onChange={(event) => setLevel(event.target.value)}
                        >
                          <option value="">未给等级</option>
                          {invalidLevel && (
                            <option value={level}>等级需要重新选择</option>
                          )}
                          {levels.map((entry) => (
                            <option
                              key={String(entry.key)}
                              value={String(entry.key)}
                            >
                              {String(entry.label ?? entry.key)}
                            </option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                  <Button
                    type="submit"
                    disabled={!retract && (!statement.trim() || invalidLevel)}
                  >
                    预览修改
                  </Button>
                </fieldset>
              </form>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
