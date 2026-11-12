import { useState } from "react"
import type { CapabilityPublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"

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
          onChange={(event) => setSearch(event.target.value)}
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
          {!groups.size && (
            <p className="text-sm text-muted-foreground">没有可添加的能力</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
