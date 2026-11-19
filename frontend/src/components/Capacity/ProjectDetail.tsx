import { useQuery } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { CapacityService, type ProjectImpactReason } from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { demandOutcomes, demandStatuses } from "./DemandsDetail"
import { ProjectCreate } from "./ProjectCreate"
import { isCapacityAccessDenied } from "./shared"

const impactLabels: Record<ProjectImpactReason, string> = {
  partial: "只能承接一部分",
  not_now: "暂时不能承接",
  late_commitment: "承诺完成日期晚于需求窗口",
  dependency: "计划开始日期早于前置角色完成后的最早开始日",
}

export function Projects({
  orgId,
  projectId,
}: {
  orgId: string
  projectId?: string
}) {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const open = (id?: string) =>
    navigate({ to: "/capacity", search: { tab: "projects", project: id } })
  if (projectId)
    return (
      <ProjectDetail
        orgId={orgId}
        projectId={projectId}
        back={() => void open()}
      />
    )
  if (creating)
    return (
      <ProjectCreate
        orgId={orgId}
        back={() => setCreating(false)}
        created={(id) => {
          setCreating(false)
          void open(id)
        }}
      />
    )
  return <ProjectList orgId={orgId} create={() => setCreating(true)} />
}

function ProjectList({ orgId, create }: { orgId: string; create: () => void }) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "projects"],
    queryFn: async () =>
      (await CapacityService.listProjects({ path: { org_id: orgId } })).data,
  })
  return (
    <section aria-label="项目需求列表" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">项目需求</h2>
        <Button onClick={create}>新建项目需求</Button>
      </div>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : query.data.length ? (
        query.data.map((project) => (
          <article key={project.id} className="space-y-2 rounded-xl border p-5">
            <Link
              className="font-semibold underline underline-offset-4"
              to="/capacity"
              search={{ tab: "projects", project: project.id }}
            >
              {project.title}
            </Link>
            <p className="text-sm text-muted-foreground">
              {project.requesting_department_name} · 提交于{" "}
              {dateLabel(project.created_at)}
            </p>
          </article>
        ))
      ) : (
        <p className="rounded-xl border border-dashed p-8 text-center text-muted-foreground">
          暂无发出的项目需求。
        </p>
      )}
    </section>
  )
}

function ProjectDetail({
  orgId,
  projectId,
  back,
}: {
  orgId: string
  projectId: string
  back: () => void
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "project", projectId],
    retry: (count, error) => !isCapacityAccessDenied(error) && count < 3,
    queryFn: async () =>
      (
        await CapacityService.getProject({
          path: { org_id: orgId, project_id: projectId },
        })
      ).data,
  })
  const project = query.data
  return (
    <div className="space-y-5">
      <Button variant="outline" onClick={back}>
        返回项目列表
      </Button>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        isCapacityAccessDenied(query.error) ? (
          <p role="status">未找到这个项目需求，或你没有权限查看。</p>
        ) : (
          <ProfileError retry={() => query.refetch()} />
        )
      ) : (
        project && (
          <>
            <section
              aria-label="项目详情"
              className="space-y-3 rounded-xl border p-5"
            >
              <h2 className="text-xl font-semibold">{project.title}</h2>
              <p className="text-sm text-muted-foreground">
                需求方 · {project.requesting_department_name}
              </p>
              {project.description && (
                <p className="whitespace-pre-wrap break-words">
                  {project.description}
                </p>
              )}
              <ul className="divide-y border-t">
                {project.roles.map((role) => (
                  <li
                    key={role.id}
                    className="flex flex-wrap items-center justify-between gap-2 py-3"
                  >
                    <div className="space-y-1">
                      <p className="font-medium">
                        {role.role_name} · {role.target_department_name}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {role.window_start} 至 {role.window_end}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">
                        {demandStatuses[role.status]}
                        {role.outcome
                          ? ` · ${demandOutcomes[role.outcome]}`
                          : ""}
                      </Badge>
                      {role.demand && (
                        <Button asChild variant="outline" size="sm">
                          <Link
                            to="/capacity"
                            search={{ tab: "outgoing", demand: role.id }}
                          >
                            查看角色需求
                          </Link>
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <section
              aria-label="分阶段时间表"
              className="space-y-3 rounded-xl border p-5"
            >
              <h3 className="font-semibold">分阶段时间表</h3>
              <p className="text-sm text-muted-foreground">
                预计完成{" "}
                {project.schedule.forecast_unconfirmed
                  ? "待确认"
                  : (project.schedule.forecast_end ?? "超出日期范围")}{" "}
                · 需求窗口截至 {project.schedule.window_end}
              </p>
              {project.schedule.stages.map((stage, index) => (
                <div
                  key={stage[0].role.id}
                  className="overflow-x-auto rounded-lg border"
                >
                  <table className="w-full text-left text-sm">
                    <caption className="bg-muted/40 px-3 py-2 text-left font-medium">
                      第 {index + 1} 阶段
                    </caption>
                    <thead>
                      <tr className="border-b">
                        {[
                          "角色与部门",
                          "计划起止",
                          "前置约束最早开始",
                          "预计起止",
                        ].map((heading) => (
                          <th scope="col" className="px-3 py-2" key={heading}>
                            {heading}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {stage.map(
                        ({
                          role,
                          planned_start,
                          planned_end,
                          earliest_start,
                          forecast_start,
                          forecast_end,
                          forecast_unconfirmed,
                        }) => (
                          <tr key={role.id}>
                            <th scope="row" className="px-3 py-2 font-medium">
                              {role.role_name} · {role.target_department_name}
                            </th>
                            <td className="px-3 py-2">
                              {planned_start} 至 {planned_end}
                            </td>
                            <td className="px-3 py-2">
                              {earliest_start ?? "—"}
                            </td>
                            <td className="px-3 py-2">
                              {forecast_unconfirmed
                                ? "待确认"
                                : forecast_start && forecast_end
                                  ? `${forecast_start} 至 ${forecast_end}`
                                  : "超出日期范围"}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              ))}
            </section>
            {project.schedule.impacts.length > 0 && (
              <section
                aria-label="项目影响"
                className="space-y-3 rounded-xl border bg-muted/30 p-5"
              >
                <h3 className="font-semibold">项目影响</h3>
                <p>
                  {project.schedule.forecast_unconfirmed
                    ? "前置角色暂未给出可承接日期，项目预计完工日待确认。"
                    : `预计比需求窗口晚 ${project.schedule.delay_days} 天。`}
                </p>
                <ul className="space-y-3">
                  {project.schedule.impacts.map(
                    ({ role, reasons, downstream }) => (
                      <li
                        className="border-l-2 border-current/20 pl-3"
                        key={role.id}
                      >
                        <p className="font-medium">
                          {role.role_name} · {role.target_department_name}
                        </p>
                        <p>
                          {reasons
                            .map((reason) => impactLabels[reason])
                            .join("；")}
                        </p>
                        {downstream.length > 0 && (
                          <p className="text-sm text-muted-foreground">
                            受影响的后续角色 ·{" "}
                            {downstream
                              .map(
                                (item) =>
                                  `${item.role_name}（${item.target_department_name}）`,
                              )
                              .join("、")}
                          </p>
                        )}
                      </li>
                    ),
                  )}
                </ul>
              </section>
            )}
          </>
        )
      )}
    </div>
  )
}
