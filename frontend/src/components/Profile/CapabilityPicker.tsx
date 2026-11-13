import { useMutation } from "@tanstack/react-query"
import { useId, useState } from "react"
import { CapabilitiesService, type CapabilityPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useOrg } from "@/hooks/useOrg"

export function CapabilityPicker({
  capabilities,
  onSelect,
  onClose,
}: {
  capabilities: Pick<CapabilityPublic, "key" | "name" | "category">[]
  onSelect: (key: string) => void
  onClose: () => void
}) {
  const [search, setSearch] = useState("")
  const nameId = useId()
  const { org, employeeId } = useOrg()
  const [suggesting, setSuggesting] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [reason, setReason] = useState("")
  const suggest = useMutation({
    mutationFn: () =>
      CapabilitiesService.suggestNewCapability({
        path: { org_id: org!.id },
        body: { name, description, reason },
      }),
  })
  const groups = new Map<string, typeof capabilities>()
  for (const capability of capabilities) {
    if (
      !`${capability.name} ${capability.category ?? ""}`
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase())
    )
      continue
    const category = capability.category ?? "其他能力"
    groups.set(category, [...(groups.get(category) ?? []), capability])
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogTitle>添加能力</DialogTitle>
        <DialogDescription>从当前词典中搜索并选择能力。</DialogDescription>
        <Input
          aria-label="搜索能力"
          placeholder="搜索能力名称或分类"
          value={search}
          disabled={suggest.isPending}
          onChange={(event) => {
            setSearch(event.target.value)
            setSuggesting(false)
            suggest.reset()
          }}
        />
        <div className="max-h-80 space-y-4 overflow-y-auto">
          {Array.from(groups, ([category, entries]) => (
            <section key={category} aria-label={category} className="space-y-2">
              <h3 className="text-sm font-medium text-muted-foreground">
                {category}
              </h3>
              {entries.map((capability) => (
                <Button
                  key={capability.key}
                  variant="outline"
                  className="h-auto w-full justify-start whitespace-normal text-left"
                  onClick={() => onSelect(capability.key)}
                >
                  {capability.name}
                </Button>
              ))}
            </section>
          ))}
          {!groups.size && !search.trim() && (
            <p className="text-sm text-muted-foreground">没有可添加的能力</p>
          )}
          {!groups.size && !!search.trim() && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground break-words">
                词典里没有『{search.trim()}』，可以建议企业管理员新增这项能力
              </p>
              {suggest.isSuccess ? (
                <p role="status">{suggest.data.data.message}</p>
              ) : suggesting ? (
                <form
                  className="space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault()
                    suggest.mutate()
                  }}
                >
                  <fieldset disabled={suggest.isPending} className="space-y-3">
                    <label htmlFor={nameId} className="block space-y-1 text-sm">
                      <span>能力名称</span>
                      <Input
                        id={nameId}
                        required
                        maxLength={40}
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                      />
                    </label>
                    <label className="block space-y-1 text-sm">
                      <span>能力说明</span>
                      <textarea
                        required
                        maxLength={300}
                        value={description}
                        className="min-h-20 w-full rounded-md border p-2"
                        onChange={(event) => setDescription(event.target.value)}
                      />
                    </label>
                    <label className="block space-y-1 text-sm">
                      <span>为什么需要</span>
                      <textarea
                        required
                        maxLength={300}
                        value={reason}
                        className="min-h-20 w-full rounded-md border p-2"
                        onChange={(event) => setReason(event.target.value)}
                      />
                    </label>
                    <SetupError error={suggest.error} />
                    <Button
                      type="submit"
                      disabled={
                        !name.trim() || !description.trim() || !reason.trim()
                      }
                    >
                      {suggest.isPending ? "正在提交…" : "提交建议"}
                    </Button>
                  </fieldset>
                </form>
              ) : employeeId && org ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setName(search.trim().slice(0, 40))
                    setSuggesting(true)
                  }}
                >
                  建议新增这项能力
                </Button>
              ) : null}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
