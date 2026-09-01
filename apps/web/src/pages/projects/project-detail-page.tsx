import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useProject, useProjectGraph } from "../../hooks/useProjects";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../../components/common/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { MilestoneList } from "../../components/project/milestone-list";
import { TaskTable } from "../../components/project/task-table";
import { ProjectGraphView } from "../../components/workflow/project-graph";
import { enumLabel } from "../../utils/labels";
import { eid, formatDateTime } from "../../utils/format";
import { PROJECT_STATUS_VARIANT, ARTIFACT_STATUS_VARIANT } from "../../utils/status";

export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);

  const projectQuery = useProject(projectId);
  const graphQuery = useProjectGraph(projectId);
  const employeesQuery = useEmployees();

  if (projectQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (projectQuery.isError || !projectQuery.data) {
    return <ErrorState error={projectQuery.error} onRetry={() => projectQuery.refetch()} />;
  }
  const project = projectQuery.data;

  return (
    <div className="space-y-6">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{project.name}</h1>
          <span className="font-mono text-xs text-muted-foreground">{eid(project.id)}</span>
          <Badge variant={PROJECT_STATUS_VARIANT[project.status]}>
            {enumLabel(t, "project:status", project.status)}
          </Badge>
        </div>
        {project.description ? (
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{project.description}</p>
        ) : null}
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t("project:createdAt", { time: formatDateTime(project.created_at) })} ·{" "}
          {t("project:updatedAt", { time: formatDateTime(project.updated_at) })}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>{t("project:milestones")}</CardTitle>
          </CardHeader>
          <CardContent>
            <MilestoneList milestones={project.milestones} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("project:workflow")}</CardTitle>
          </CardHeader>
          <CardContent>
            {graphQuery.isLoading ? (
              <Skeleton className="h-80 w-full" />
            ) : graphQuery.isError ? (
              <p className="text-sm text-muted-foreground">{t("project:workflowUnavailable")}</p>
            ) : graphQuery.data ? (
              <ProjectGraphView graph={graphQuery.data} />
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("project:tasks")}</CardTitle>
        </CardHeader>
        <CardContent>
          <TaskTable tasks={project.tasks} employees={employeesQuery.data ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("project:artifacts")}</CardTitle>
        </CardHeader>
        <CardContent>
          {project.artifacts.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("project:artifactsEmpty")}</p>
          ) : (
            <ul className="space-y-2">
              {project.artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <Link
                    to={`/artifacts/${artifact.id}`}
                    className="flex items-center gap-3 rounded-md border border-border px-3 py-2 transition-colors hover:border-foreground/20"
                  >
                    <Badge variant="muted">
                      {enumLabel(t, "artifact:type", artifact.type)}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {artifact.title}
                    </span>
                    <Badge variant={ARTIFACT_STATUS_VARIANT[artifact.status]}>
                      {enumLabel(t, "artifact:status", artifact.status)}
                    </Badge>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      v{artifact.version}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
