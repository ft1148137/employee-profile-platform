import { useMutation } from "@tanstack/react-query"
import { useId, useRef } from "react"
import {
  AdminAssistantService,
  type AdminTurnResponse,
  type FileUploadCard as Card,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function FileUploadCard({
  orgId,
  sessionId,
  card,
  disabled,
  onUploaded,
  onUploading,
}: {
  orgId: string
  sessionId: string
  card: Card
  disabled: boolean
  onUploaded: (response: AdminTurnResponse) => void
  onUploading: (busy: boolean) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const id = useId()
  const upload = useMutation({
    onMutate: () => onUploading(true),
    onSettled: () => onUploading(false),
    mutationFn: async () => {
      const file = input.current?.files?.[0]
      if (!file) throw new Error("请先选择名单文件")
      return (
        await AdminAssistantService.assistantUploadFile({
          query: { org_id: orgId },
          path: { session_id: sessionId },
          body: { kind: card.kind, file },
        })
      ).data
    },
    onSuccess: (response) => {
      if (input.current) input.current.value = ""
      onUploaded(response)
    },
  })
  return (
    <form
      aria-label="上传名单"
      className="space-y-3 rounded-xl border border-primary/25 bg-background p-4"
      onSubmit={(event) => {
        event.preventDefault()
        upload.mutate()
      }}
    >
      <label htmlFor={id} className="font-semibold">
        选择员工名单
      </label>
      <p className="text-sm text-muted-foreground">
        支持 Excel、CSV，最多 5 MB。上传后先核对预览，点击确认才会更新名单。
      </p>
      <Input
        id={id}
        ref={input}
        type="file"
        accept=".xlsx,.csv"
        required
        disabled={disabled || upload.isPending}
      />
      <Button disabled={disabled || upload.isPending}>
        {upload.isPending ? "正在预览…" : "上传并预览"}
      </Button>
      <SetupError error={upload.error} />
    </form>
  )
}
