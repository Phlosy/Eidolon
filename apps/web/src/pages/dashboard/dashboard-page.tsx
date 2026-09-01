import { CircleDollarSign, FileStack, FolderKanban, ListTodo, Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useDashboardStats } from "../../hooks/useDashboardStats";
import { StatCard } from "../../components/company/stat-card";
import { PageHeader } from "../../components/common/states";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { ActivityFeed } from "../../features/activity-feed/activity-feed";
import { formatUsd } from "../../utils/format";

export function DashboardPage() {
  const { t } = useTranslation();
  const stats = useDashboardStats();

  return (
    <div>
      <PageHeader title={t("dashboard:title")} description={t("dashboard:description")} />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label={t("dashboard:stats.employeesOnline")}
          value={`${stats.employeesOnline}/${stats.employeesTotal}`}
          hint={t("dashboard:stats.employeesOnlineHint")}
          icon={Users}
          loading={stats.isLoading}
        />
        <StatCard
          label={t("dashboard:stats.activeProjects")}
          value={stats.activeProjects}
          icon={FolderKanban}
          loading={stats.isLoading}
        />
        <StatCard
          label={t("dashboard:stats.tasksInProgress")}
          value={stats.tasksInProgress}
          icon={ListTodo}
          loading={stats.isLoading}
        />
        <StatCard
          label={t("dashboard:stats.artifacts")}
          value={stats.artifactsCount}
          icon={FileStack}
          loading={stats.isLoading}
        />
        <StatCard
          label={t("dashboard:stats.runtimeCost")}
          value={formatUsd(stats.runtimeCostUsd)}
          hint={t("dashboard:stats.runtimeCostHint")}
          icon={CircleDollarSign}
          loading={stats.isLoading}
        />
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>{t("dashboard:recentActivity")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ActivityFeed limit={30} />
        </CardContent>
      </Card>
    </div>
  );
}
