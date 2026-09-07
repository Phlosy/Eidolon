import { useTranslation } from "react-i18next";
import { useCurrentPositionFit } from "../../hooks/usePositionFit";
import { FitAnalysis } from "./fit-analysis";
import { EmptyState } from "../common/states";
import { Skeleton } from "../common/skeleton";

/** Employee Detail → 岗位匹配 tab：当前任职职位的 Fit（无任职/未配置则中性提示）。 */
export function CurrentPositionFitTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const fitQuery = useCurrentPositionFit(employeeId);

  if (fitQuery.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (fitQuery.isError) {
    return <EmptyState title={t("employee:tabs.positionFit")} />;
  }
  if (!fitQuery.data || fitQuery.data.fit_status === "NOT_EVALUABLE") {
    return (
      <EmptyState
        title={t("employee:tabs.positionFit")}
        hint={t("position:positions.noCurrentFitHint")}
      />
    );
  }
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">{t("position:positions.currentPositionFit")}</h3>
      <FitAnalysis result={fitQuery.data} employeeId={employeeId} />
    </div>
  );
}
