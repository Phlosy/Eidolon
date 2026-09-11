import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { BadgeCheck, BookOpen, ScrollText, ShieldQuestion, Users } from "lucide-react";
import { useEmployeeRoleContext } from "../../hooks/useEmployees";
import { Badge } from "../common/badge";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { Panel, SectionHeader } from "../shared/panel";

/**
 * Role Context tab（M2.2）—— 履职上下文的**只读**展示。
 *
 * 三件它刻意展示/不展示的事：
 *
 * 1. **展示**"公司希望你负责什么、并授权你做什么"：职位职责、生效的管理授权、
 *    建议阅读的资源（含指针是否已解析）。
 * 2. **不展示**这个人的能力分数：期望只列引用（competency code + required/preferred），
 *    分值请在能力页看（`RoleContext` 响应里根本没有 score/level/rank）。
 * 3. **不给建议**：这里没有"你应该先做什么"—— 学什么、怎么组织工作由 Agent 自己判断。
 */
export function RoleContextTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const query = useEmployeeRoleContext(employeeId);

  if (query.isLoading) return <Skeleton className="h-72 w-full" />;
  if (query.isError || !query.data) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  const { context, resources, live_projects: liveProjects } = query.data;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel className="p-5">
        <SectionHeader
          title={t("employee:roleContext.responsibilities")}
          description={context.position_code ?? t("employee:roleContext.noPosition")}
        />
        {context.responsibilities.length === 0 ? (
          <EmptyState title={t("employee:roleContext.noResponsibilities")} />
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {context.responsibilities.map((item) => (
              <li key={item} className="flex gap-2 text-muted-foreground">
                <ScrollText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        )}
        {context.advisory_scope.length > 0 ? (
          <p className="mt-4 text-[11px] leading-5 text-muted-foreground">
            {t("employee:roleContext.advisoryScopeHint")}
          </p>
        ) : null}
        {context.advisory_scope.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {context.advisory_scope.map((item) => (
              <Badge key={item} variant="muted">
                {item}
              </Badge>
            ))}
          </div>
        ) : null}
      </Panel>

      <Panel className="p-5">
        <SectionHeader
          title={t("employee:roleContext.authority")}
          description={t("employee:roleContext.authorityHint")}
        />
        {context.authority.length === 0 ? (
          <EmptyState title={t("employee:roleContext.noAuthority")} />
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {context.authority.map((grant) => (
              <li
                key={`${grant.kind}-${grant.scope_kind}-${grant.scope_ref}`}
                className="flex flex-wrap items-center gap-2"
              >
                <BadgeCheck className="h-3.5 w-3.5 text-success" />
                <span className="font-medium">{t(`employee:authority.${grant.kind}`)}</span>
                <Badge variant="info">{t(`employee:scope.${grant.scope_kind}`)}</Badge>
                {grant.max_amount != null ? (
                  <span className="type-telemetry text-[10px] text-muted-foreground">
                    ≤ {grant.max_amount.toLocaleString()}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-[11px] leading-5 text-muted-foreground">
          {t("employee:roleContext.defaultDenyHint")}
        </p>
      </Panel>

      <Panel className="p-5">
        <SectionHeader
          title={t("employee:roleContext.expectations")}
          description={t("employee:roleContext.expectationsHint")}
        />
        {context.expectations.length === 0 ? (
          <EmptyState title={t("employee:roleContext.noExpectations")} />
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {context.expectations.map((item) => (
              <li key={item.competency_code} className="flex flex-wrap items-center gap-2">
                <Badge variant={item.critical ? "warning" : "muted"}>{item.requirement_type}</Badge>
                <span>{item.competency_code}</span>
                {item.critical ? (
                  <span className="text-[10px] text-warning">
                    {t("employee:roleContext.critical")}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel className="p-5">
        <SectionHeader
          title={t("employee:roleContext.resources")}
          description={t("employee:roleContext.resourcesHint")}
        />
        {resources.length === 0 ? (
          <EmptyState title={t("employee:roleContext.noResources")} />
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {resources.map((item) => (
              <li key={`${item.kind}-${item.ref}`} className="flex flex-wrap items-center gap-2">
                <BookOpen className="h-3.5 w-3.5 text-primary" />
                <span className="font-medium">{item.ref}</span>
                <Badge variant="muted">{t(`employee:resourceKind.${item.kind}`)}</Badge>
                <Badge
                  variant={
                    item.resolution === "resolved"
                      ? "success"
                      : item.resolution === "advisory"
                        ? "muted"
                        : "warning"
                  }
                >
                  {t(`employee:resolution.${item.resolution}`)}
                </Badge>
                {item.pointer ? (
                  <span className="type-telemetry text-[10px] text-muted-foreground">
                    {item.pointer}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel className="p-5 xl:col-span-2">
        <SectionHeader
          title={t("employee:roleContext.contextFacts")}
          description={t("employee:roleContext.contextFactsHint")}
        />
        <div className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
          <div>
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <Users className="h-3.5 w-3.5" /> {t("employee:roleContext.directReports")}
            </p>
            <p className="mt-1 font-medium">
              {context.direct_reports.length > 0 ? context.direct_reports.join(", ") : "—"}
            </p>
          </div>
          <div>
            <p className="text-muted-foreground">{t("employee:roleContext.knowledgeScopes")}</p>
            <p className="mt-1 font-medium">{context.knowledge_scopes.join(" · ")}</p>
          </div>
          <div>
            <p className="text-muted-foreground">{t("employee:roleContext.policyKeys")}</p>
            <p className="mt-1 font-medium">
              {context.company_policy_keys.length > 0
                ? context.company_policy_keys.join(" · ")
                : "—"}
            </p>
          </div>
        </div>
        <div className="mt-4">
          <p className="text-xs text-muted-foreground">{t("employee:roleContext.liveProjects")}</p>
          {liveProjects.length === 0 ? (
            <p className="mt-1 text-xs">{t("employee:roleContext.noLiveProjects")}</p>
          ) : (
            <ul className="mt-2 flex flex-wrap gap-2">
              {liveProjects.map((project) => (
                <li key={project.project_id}>
                  <Link
                    to={`/projects/${project.project_id}`}
                    className="rounded-md border border-border px-2.5 py-1 text-xs hover:bg-accent"
                  >
                    #{project.project_id} {project.name}
                    <span className="ml-2 text-[10px] text-muted-foreground">{project.status}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="mt-4 flex items-start gap-1.5 text-[11px] leading-5 text-muted-foreground">
          <ShieldQuestion className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("employee:roleContext.readOnlyHint")}
        </p>
      </Panel>
    </div>
  );
}
