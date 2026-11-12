import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import {
  ExternalIdentitiesService,
  type ExternalIdentityPublic,
  type IdentityLink,
} from "@/client"
import { ConfirmActionDialog } from "@/components/Admin/ConfirmActionDialog"
import { PageDraft } from "@/components/AdminAssistant/PageDraft"
import { usePageDraft } from "@/components/AdminAssistant/usePageDraft"
import { SetupError } from "@/components/Setup/SetupError"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmployeePicker } from "./EmployeePicker"
import { UnlinkDialog } from "./UnlinkDialog"

export function IdentityTable({
  orgId,
  inWizard = false,
  employeeId,
  initialSource,
}: {
  orgId: string
  inWizard?: boolean
  employeeId?: string
  initialSource?: string
}) {
  const client = useQueryClient()
  const linkDraft = usePageDraft(orgId, "link_identity")
  const unlinkDraft = usePageDraft(orgId, "unlink_identity")
  const drafts = [linkDraft, unlinkDraft].filter(
    (entry, index, all) =>
      entry.draft &&
      all.findIndex((other) => other.draft?.id === entry.draft?.id) === index,
  )
  const draftItems = drafts.flatMap((entry) => entry.draft?.items ?? [])
  const [status, setStatus] = useState<"unlinked" | "linked">(
    employeeId ? "linked" : "unlinked",
  )
  const [q, setQ] = useState("")
  const [identityId, setIdentityId] = useState("")
  const [source, setSource] = useState(
    initialSource ?? (employeeId ? "jira" : ""),
  )
  const [skip, setSkip] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [picker, setPicker] = useState<ExternalIdentityPublic | null>(null)
  const [unlinking, setUnlinking] = useState<ExternalIdentityPublic | null>(
    null,
  )
  const [message, setMessage] = useState("")
  const identities = useQuery({
    queryKey: ["identities", orgId, "summary", employeeId],
    enabled: !employeeId,
    queryFn: async () => {
      const first = (
        await ExternalIdentitiesService.identitiesGetIdentities({
          path: { org_id: orgId },
          query: {
            status: "all",
            limit: 200,
            source: employeeId ? "jira" : undefined,
          },
        })
      ).data
      const rows = [...first.data]
      for (let offset = 200; offset < first.count; offset += 200) {
        rows.push(
          ...(
            await ExternalIdentitiesService.identitiesGetIdentities({
              path: { org_id: orgId },
              query: {
                status: "all",
                skip: offset,
                limit: 200,
                source: employeeId ? "jira" : undefined,
              },
            })
          ).data.data,
        )
      }
      return rows
    },
  })
  const rows = identities.data ?? []
  const unlinked = rows.filter((row) => !row.employee)
  const linked = rows.filter(
    (row) => row.employee && (!employeeId || row.employee.id === employeeId),
  )
  const page = useQuery({
    queryKey: ["identities", orgId, status, source, q, skip, employeeId],
    queryFn: async () =>
      (
        await ExternalIdentitiesService.identitiesGetIdentities({
          path: { org_id: orgId },
          query: {
            status,
            source: source || undefined,
            q,
            skip,
            limit: 50,
            employee_id: status === "linked" ? employeeId : undefined,
          },
        })
      ).data,
  })
  const visible = page.data?.data ?? []
  const count = page.data?.count ?? 0
  const candidates = visible.filter(
    (row) => row.candidate && (!employeeId || row.candidate.id === employeeId),
  )
  const links = visible
    .filter(
      (row) =>
        selected.includes(row.id) &&
        row.candidate &&
        (!employeeId || row.candidate.id === employeeId),
    )
    .map((row) => ({
      identity_id: row.id,
      employee_id: row.candidate!.id,
    }))
  async function refresh() {
    setSelected([])
    setIdentityId("")
    setSkip(0)
    await client.invalidateQueries({ queryKey: ["identities", orgId] })
  }
  const link = useMutation({
    mutationFn: (links: IdentityLink[]) =>
      ExternalIdentitiesService.identitiesPostLinks({
        path: { org_id: orgId },
        body: { links },
      }),
    onSuccess: async ({ data }) => {
      setPicker(null)
      setMessage(
        `已关联 ${data.linked} 个账号，已补充 ${data.counts.evidence_added} 条证据`,
      )
      await refresh()
    },
  })
  const unlink = useMutation({
    mutationFn: (identityId: string) =>
      ExternalIdentitiesService.identitiesPostUnlink({
        path: { org_id: orgId, identity_id: identityId },
      }),
    onSuccess: async ({ data }) => {
      setUnlinking(null)
      setMessage(`已撤销 ${data.evidence_retracted} 条证据`)
      await refresh()
    },
  })
  const actionPending = link.isPending || unlink.isPending
  const pending = actionPending || linkDraft.blocked || unlinkDraft.blocked
  function resetSelection() {
    setSelected([])
    setSkip(0)
  }
  return (
    <div className="min-w-0 space-y-5">
      <SetupError error={linkDraft.error ?? unlinkDraft.error} />
      {drafts.map((entry) => (
        <PageDraft
          key={entry.draft!.id}
          orgId={orgId}
          draft={entry.draft}
          error={entry.error}
        />
      ))}
      <div className="space-y-1 rounded-lg border bg-muted/20 p-4 text-sm text-muted-foreground">
        <p>只有关联到员工的账号，它的记录才会算到这名员工头上。</p>
        <p>系统按邮箱或姓名给出候选，需要你确认；同名的人不会自动合并。</p>
        <p>服务账号、公用账号、外部人员可以不关联。</p>
      </div>
      {message && (
        <p
          role="status"
          className="rounded-lg border border-primary/30 bg-primary/5 p-3"
        >
          {message}
        </p>
      )}
      <SetupError
        error={identities.error ?? page.error ?? (!picker ? link.error : null)}
      />
      {(identities.isError || page.isError) && (
        <Button
          onClick={() => {
            identities.refetch()
            page.refetch()
          }}
        >
          重试
        </Button>
      )}
      <Tabs
        value={status}
        onValueChange={(value) => {
          setStatus(value === "linked" ? "linked" : "unlinked")
          resetSelection()
        }}
      >
        <TabsList aria-label="账号关联状态">
          <TabsTrigger value="unlinked" disabled={actionPending}>
            未关联{!employeeId && `（${unlinked.length}）`}
          </TabsTrigger>
          <TabsTrigger value="linked" disabled={actionPending}>
            已关联{!employeeId && `（${linked.length}）`}
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="搜索账号、名字或邮箱"
          placeholder="搜索账号、名字或邮箱"
          className="sm:max-w-xs"
          value={q}
          disabled={actionPending}
          onChange={(e) => {
            setQ(e.target.value)
            setIdentityId("")
            resetSelection()
          }}
        />
        {!employeeId && (
          <select
            aria-label="按来源筛选"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            value={source}
            disabled={actionPending}
            onChange={(e) => {
              setSource(e.target.value)
              resetSelection()
            }}
          >
            <option value="">全部来源</option>
            {[
              ...new Map(
                rows.map((row) => [row.source, row.source_label]),
              ).entries(),
            ].map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
        {!employeeId && status === "unlinked" && (
          <Button
            disabled={pending || !links.length}
            onClick={() => link.mutate(links)}
          >
            确认所选候选{links.length ? `（${links.length}）` : ""}
          </Button>
        )}
      </div>
      {(!employeeId && identities.isPending) || page.isPending ? (
        <p role="status">正在加载账号…</p>
      ) : employeeId && status === "unlinked" ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-2 text-sm" htmlFor="link-employee-identity">
            <span className="block">关联账号</span>
            <select
              id="link-employee-identity"
              className="h-10 max-w-full rounded-md border bg-background px-3"
              value={identityId}
              disabled={pending || page.isError}
              onChange={(event) => setIdentityId(event.target.value)}
            >
              <option value="">请选择未关联的账号</option>
              {visible.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.display_name ?? row.external_id} · {row.external_id}
                </option>
              ))}
            </select>
          </label>
          <ConfirmActionDialog
            title="关联到此员工"
            disabled={pending || !visible.some((row) => row.id === identityId)}
            description={`确认把账号「${visible.find((row) => row.id === identityId)?.external_id ?? ""}」关联到当前员工？这个账号的工作记录将作为这名员工的证据。`}
            action={() =>
              ExternalIdentitiesService.identitiesPostLinks({
                path: { org_id: orgId },
                body: {
                  links: [{ identity_id: identityId, employee_id: employeeId }],
                },
              })
            }
            onSuccess={() => {
              refresh()
              setStatus("linked")
            }}
          />
          {!visible.length && <p>没有符合条件的账号</p>}
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {status === "unlinked" && (
                <TableHead>
                  <Checkbox
                    aria-label="选择本页全部候选"
                    disabled={pending || !candidates.length}
                    checked={
                      candidates.length > 0 &&
                      candidates.every((row) => selected.includes(row.id))
                    }
                    onCheckedChange={(checked) =>
                      setSelected(
                        checked ? candidates.map((row) => row.id) : [],
                      )
                    }
                  />
                </TableHead>
              )}
              <TableHead>来源</TableHead>
              <TableHead>账号</TableHead>
              {status === "unlinked" ? (
                <>
                  <TableHead>来源里的名字</TableHead>
                  <TableHead>邮箱</TableHead>
                  <TableHead>记录数</TableHead>
                  <TableHead>候选</TableHead>
                </>
              ) : (
                <>
                  <TableHead>员工</TableHead>
                  <TableHead>关联时间</TableHead>
                </>
              )}
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row) => (
              <TableRow
                key={row.id}
                data-assistant-suggested={
                  draftItems.some((item) => item.args.identity_id === row.id) ||
                  undefined
                }
                className={
                  draftItems.some((item) => item.args.identity_id === row.id)
                    ? "bg-primary/10"
                    : undefined
                }
              >
                {status === "unlinked" && (
                  <TableCell>
                    <Checkbox
                      aria-label={`选择账号 ${row.external_id}`}
                      disabled={
                        pending ||
                        !row.candidate ||
                        (!!employeeId && row.candidate.id !== employeeId)
                      }
                      checked={selected.includes(row.id)}
                      onCheckedChange={(checked) =>
                        setSelected(
                          checked
                            ? [...selected, row.id]
                            : selected.filter((id) => id !== row.id),
                        )
                      }
                    />
                  </TableCell>
                )}
                <TableCell>{row.source_label}</TableCell>
                <TableCell className="font-mono text-xs">
                  {row.external_id}
                  {draftItems
                    .filter((item) => item.args.identity_id === row.id)
                    .map((item, i) => (
                      <p
                        key={i}
                        className="mt-2 font-sans font-medium text-primary"
                      >
                        助手建议：
                        {item.function === "link_identity"
                          ? "关联到所选员工"
                          : "解除关联"}
                      </p>
                    ))}
                </TableCell>
                {status === "unlinked" ? (
                  <>
                    <TableCell>{row.display_name ?? "未提供"}</TableCell>
                    <TableCell>{row.email ?? "未提供"}</TableCell>
                    <TableCell>{row.record_count}</TableCell>
                    <TableCell>
                      {row.candidate ? (
                        <div className="space-y-1">
                          <p className="font-medium">
                            {row.candidate.display_name}（
                            {row.candidate.employee_code ?? "无工号"}）
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {row.candidate.department?.name ?? "未分配部门"}
                          </p>
                          <Badge
                            variant={
                              row.candidate.reason === "email"
                                ? "secondary"
                                : "outline"
                            }
                          >
                            {row.candidate.reason === "email"
                              ? "邮箱相同"
                              : "姓名相同，请核对"}
                          </Badge>
                        </div>
                      ) : (
                        "暂无候选，请选择员工"
                      )}
                    </TableCell>
                  </>
                ) : (
                  <>
                    <TableCell>
                      {row.employee?.display_name}（
                      {row.employee?.employee_code ?? "无工号"}）
                      <p className="text-xs text-muted-foreground">
                        {row.employee?.department?.name ?? "未分配部门"}
                      </p>
                    </TableCell>
                    <TableCell>
                      {row.linked_at
                        ? new Date(row.linked_at).toLocaleString("zh-CN", {
                            year: "numeric",
                            month: "2-digit",
                            day: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "未记录"}
                      {row.linked_by === "system_worker:email_match" && (
                        <p className="text-xs text-muted-foreground">
                          按邮箱自动关联
                        </p>
                      )}
                    </TableCell>
                  </>
                )}
                <TableCell>
                  <div className="flex gap-2">
                    {status === "unlinked" && employeeId ? (
                      <ConfirmActionDialog
                        disabled={pending}
                        title="关联到此员工"
                        description={`确认把账号「${row.external_id}」关联到当前员工？这个账号的工作记录将作为这名员工的证据。`}
                        action={() =>
                          ExternalIdentitiesService.identitiesPostLinks({
                            path: { org_id: orgId },
                            body: {
                              links: [
                                {
                                  identity_id: row.id,
                                  employee_id: employeeId,
                                },
                              ],
                            },
                          })
                        }
                        onSuccess={() => {
                          refresh()
                        }}
                      />
                    ) : status === "unlinked" ? (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={pending || !row.candidate}
                          onClick={() =>
                            link.mutate([
                              {
                                identity_id: row.id,
                                employee_id: row.candidate!.id,
                              },
                            ])
                          }
                        >
                          确认候选
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={pending}
                          onClick={() => {
                            link.reset()
                            setPicker(row)
                          }}
                        >
                          选择员工
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={pending}
                        onClick={() => {
                          unlink.reset()
                          setUnlinking(row)
                        }}
                      >
                        解除关联
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!visible.length && (
              <TableRow>
                <TableCell
                  colSpan={status === "unlinked" ? 8 : 5}
                  className="py-10 text-center text-muted-foreground"
                >
                  没有符合条件的账号
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      )}
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          disabled={actionPending || !skip}
          onClick={() => {
            setSkip(skip - 50)
            setSelected([])
          }}
        >
          上一页
        </Button>
        <span className="text-sm text-muted-foreground">共 {count} 个账号</span>
        <Button
          variant="outline"
          disabled={actionPending || skip + 50 >= count}
          onClick={() => {
            setSkip(skip + 50)
            setSelected([])
          }}
        >
          下一页
        </Button>
      </div>
      {inWizard && identities.isSuccess && unlinked.length > 0 && (
        <p className="text-sm text-muted-foreground">
          还有 {unlinked.length} 个账号未关联，它们的记录不会算到任何人头上
        </p>
      )}
      {picker && (
        <EmployeePicker
          orgId={orgId}
          account={picker.external_id}
          pending={pending}
          error={link.error}
          onSelect={(employeeId) =>
            link.mutate([{ identity_id: picker.id, employee_id: employeeId }])
          }
          onClose={() => {
            setPicker(null)
            link.reset()
          }}
        />
      )}
      {unlinking && (
        <UnlinkDialog
          account={unlinking.external_id}
          pending={pending}
          error={unlink.error}
          onConfirm={() => unlink.mutate(unlinking.id)}
          onClose={() => setUnlinking(null)}
        />
      )}
    </div>
  )
}
