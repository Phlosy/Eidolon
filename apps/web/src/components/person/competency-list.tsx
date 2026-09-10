import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import type { EmployeeCompetencyView } from "../../types";

/**
 * 能力维度列表（T2.1 共享组件）：score 与 confidence **并列同权**，未评估显示「未评估」而非 0。
 *
 * 与后端读面契约一一对应：`score=null` ⇒ unrated（`evidence_count=0` 是"真的没有证据"，
 * 不是没算）。
 */
export function CompetencyList({
  rows,
  testId = "competency-list",
  rowTestIdPrefix = "competency-",
}: {
  rows: EmployeeCompetencyView[];
  testId?: string;
  rowTestIdPrefix?: string;
}) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-1.5" data-testid={testId}>
      {rows.map((row) => (
        <li
          key={row.competency_definition_id}
          data-testid={`${rowTestIdPrefix}${row.code}`}
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-3 py-2"
        >
          <span className="text-xs font-medium">{row.name}</span>
          {row.score === null ? (
            <Badge variant="muted">{t("person:unrated")}</Badge>
          ) : (
            <span className="font-mono text-xs">
              {row.score}
              {row.confidence !== null ? (
                <span className="ml-2 text-[11px] text-muted-foreground">
                  {t("person:confidence", { value: `${Math.round(row.confidence * 100)}%` })}
                </span>
              ) : null}
              <span className="ml-2 text-[11px] text-muted-foreground">
                {t("person:evidenceCount", { count: row.evidence_count })}
              </span>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
