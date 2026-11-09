import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useId, useRef } from "react"
import { AdminAssistantService, type SecretInputCard as Card } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function SecretInputCard({
  orgId,
  card,
  disabled,
}: {
  orgId: string
  card: Card
  disabled: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const client = useQueryClient()
  const id = useId()
  const path = { org_id: orgId, id: card.change_set_id }
  const query = useQuery({
    queryKey: ["admin-change-set", orgId, card.change_set_id],
    queryFn: async () =>
      (await AdminAssistantService.assistantGetChangeSet({ path })).data,
  })
  const save = useMutation({
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: ["admin-change-set", orgId, card.change_set_id],
      }),
    mutationFn: async () => {
      const value = input.current?.value ?? ""
      if (input.current) input.current.value = ""
      try {
        await AdminAssistantService.assistantSaveChangeSetSecret({
          path: { ...path, field: card.field },
          body: { value },
        })
      } catch {
        throw new Error("填写失败，请重新填写；若改动已结束，请重新起草。")
      }
    },
  })
  return (
    <form
      aria-label="安全输入框"
      className="space-y-3 rounded-xl border border-primary/25 bg-background p-4"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <label htmlFor={id} className="font-semibold">
        {card.title}
      </label>
      <p className="text-sm text-muted-foreground">
        仅用于这份改动，不会发送给助手，也不会写入聊天记录。
      </p>
      <Input
        ref={input}
        id={id}
        type="password"
        autoComplete="new-password"
        required
        maxLength={10000}
        disabled={disabled || save.isPending || query.data?.status !== "draft"}
      />
      <Button
        disabled={disabled || save.isPending || query.data?.status !== "draft"}
      >
        安全提交
      </Button>
      {save.isSuccess && <p role="status">已填写</p>}
      <SetupError error={save.error ?? query.error} />
    </form>
  )
}
