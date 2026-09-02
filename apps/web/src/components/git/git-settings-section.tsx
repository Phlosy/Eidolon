import { useState } from "react";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useGitOverview } from "../../hooks/useGit";
import { Button } from "../common/button";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { BuiltinGiteaCard } from "./builtin-gitea-card";
import { GitConnectionCard } from "./git-connection-card";
import { GitConnectionForm } from "./git-connection-form";

/**
 * Settings → Git: built-in Gitea lifecycle card + external Git platform
 * connections (Eidolon only connects to external platforms, never installs
 * them). GET /git polls while the builtin install is running.
 */
export function GitSettingsSection() {
  const { t } = useTranslation();
  const overviewQuery = useGitOverview();
  const [addOpen, setAddOpen] = useState(false);

  const overview = overviewQuery.data;

  return (
    <Card className="mb-6">
      <CardHeader>
        <div>
          <CardTitle>{t("git:section.title")}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t("git:section.description")}</p>
        </div>
      </CardHeader>
      <CardContent>
        {overviewQuery.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : overviewQuery.isError ? (
          <ErrorState error={overviewQuery.error} onRetry={() => overviewQuery.refetch()} />
        ) : overview ? (
          <div className="space-y-5">
            <BuiltinGiteaCard builtin={overview.builtin} />

            <div>
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {t("git:connections.title")}
                  </h4>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("git:connections.description")}
                  </p>
                </div>
                <Button size="sm" onClick={() => setAddOpen(true)}>
                  <Plus className="h-3.5 w-3.5" />
                  {t("git:connections.add")}
                </Button>
              </div>

              {overview.connections.length === 0 ? (
                <EmptyState
                  title={t("git:connections.emptyTitle")}
                  hint={t("git:connections.emptyHint")}
                />
              ) : (
                <div className="space-y-3">
                  {overview.connections.map((connection) => (
                    <GitConnectionCard key={connection.id} connection={connection} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

        <GitConnectionForm open={addOpen} onOpenChange={setAddOpen} />
      </CardContent>
    </Card>
  );
}
