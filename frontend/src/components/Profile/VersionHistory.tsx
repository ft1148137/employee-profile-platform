import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { ProfilesService } from "@/client"
import { Button } from "@/components/ui/button"
import { CapabilityList } from "./CapabilityList"
import { ProfileError, ProfileLoading } from "./QueryState"
import { viaLabels } from "./RequestTimeline"

export function VersionHistory({
  orgId,
  employeeId,
  onEvidence,
  onRequest,
}: {
  orgId: string
  employeeId: string
  onEvidence: (id: string) => void
  onRequest: (id: string) => void
}) {
  const [revision, setRevision] = useState<number | null>(null)
  const path = { org_id: orgId, employee_id: employeeId }
  const versions = useQuery({
    queryKey: ["profile-versions", orgId, employeeId],
    queryFn: async () =>
      (await ProfilesService.readProfileVersions({ path })).data,
  })
  const snapshot = useQuery({
    queryKey: ["profile-version", orgId, employeeId, revision],
    queryFn: async () =>
      (
        await ProfilesService.readProfileVersion({
          path: { ...path, revision: revision! },
        })
      ).data,
    enabled: revision !== null,
  })
  if (versions.isPending) return <ProfileLoading />
  if (versions.isError) return <ProfileError retry={() => versions.refetch()} />
  if (!versions.data.data.length)
    return <p className="py-8 text-muted-foreground">暂无已发布版本。</p>
  return (
    <div className="space-y-6">
      <ul className="divide-y rounded-lg border">
        {[...versions.data.data]
          .sort((a, b) => b.revision - a.revision)
          .map((version) => (
            <li key={version.revision}>
              <div className="px-5 pt-4 text-sm text-muted-foreground">
                {version.via && viaLabels[version.via]}
                {version.request_id && (
                  <>
                    {" "}
                    ·{" "}
                    <button
                      type="button"
                      className="text-primary underline"
                      onClick={() => onRequest(version.request_id!)}
                    >
                      来源请求
                    </button>
                  </>
                )}
              </div>
              <Button
                variant="ghost"
                aria-expanded={revision === version.revision}
                className="h-auto w-full justify-between gap-4 whitespace-normal px-5 py-4 text-left"
                onClick={() =>
                  setRevision(
                    revision === version.revision ? null : version.revision,
                  )
                }
              >
                <span>
                  r{version.revision} ·{" "}
                  {new Date(version.published_at).toLocaleString("zh-CN")}
                </span>
                <span className="text-sm text-muted-foreground">
                  发布人：{version.published_by.display_name ?? "—"}
                </span>
              </Button>
            </li>
          ))}
      </ul>
      {revision !== null && (
        <section aria-label={`r${revision} 只读快照`} className="space-y-4">
          <h2 className="text-lg font-semibold">
            r{revision} · 已发布快照（只读）
          </h2>
          {snapshot.isPending ? (
            <ProfileLoading />
          ) : snapshot.isError ? (
            <ProfileError retry={() => snapshot.refetch()} />
          ) : (
            <CapabilityList
              items={snapshot.data.items}
              onEvidence={onEvidence}
            />
          )}
        </section>
      )}
    </div>
  )
}
