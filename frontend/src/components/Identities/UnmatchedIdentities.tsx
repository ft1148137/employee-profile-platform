import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { ExternalIdentitiesService } from "@/client"
import { IdentityTable } from "@/components/Identities/IdentityTable"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

export function UnmatchedIdentities({
  orgId,
  initiallyOpen,
}: {
  orgId: string
  initiallyOpen: boolean
}) {
  const [open, setOpen] = useState(initiallyOpen)
  const query = useQuery({
    queryKey: ["identities", orgId, "unmatched-count"],
    queryFn: async () =>
      (
        await ExternalIdentitiesService.identitiesGetIdentities({
          path: { org_id: orgId },
          query: { status: "unlinked", source: "jira", limit: 1 },
        })
      ).data.count,
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <SetupError error={query.error} />
      {query.isError ? (
        <Button onClick={() => query.refetch()}>重试未匹配账号</Button>
      ) : (
        <DialogTrigger asChild>
          <Button variant="outline" disabled={query.isPending}>
            {query.data ?? "—"} 个 Jira 账号未匹配
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[85dvh] overflow-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>未匹配 Jira 账号</DialogTitle>
        </DialogHeader>
        <IdentityTable orgId={orgId} initialSource="jira" />
      </DialogContent>
    </Dialog>
  )
}
