import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import { formatDateTime, formatPercent } from "../../utils/format";
import type { KnowledgeItem, LearningRecord } from "../../types";

export function LearningRecordsList({ records }: { records: LearningRecord[] }) {
  const { t } = useTranslation();
  if (records.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("employee:learning.emptyTitle")}</p>;
  }
  return (
    <ul className="space-y-3">
      {records.map((record) => (
        <li key={record.id} className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2">
            <Badge variant={record.kind === "reflection" ? "violet" : "info"}>
              {enumLabel(t, "employee:learning.kind", record.kind)}
            </Badge>
            {record.topic ? (
              <span className="text-xs text-muted-foreground">{record.topic}</span>
            ) : null}
            <span className="ml-auto text-[11px] text-muted-foreground">
              {formatDateTime(record.created_at)}
            </span>
          </div>
          {record.problem ? <p className="mt-2 text-sm font-medium">{record.problem}</p> : null}
          {record.lesson ? (
            <p className="mt-1 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t("employee:learning.lesson")}</span>{" "}
              {record.lesson}
            </p>
          ) : null}
          {record.solution ? (
            <p className="mt-1 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t("employee:learning.solution")}</span>{" "}
              {record.solution}
            </p>
          ) : null}
          {record.confidence != null ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("employee:learning.confidence", { value: formatPercent(record.confidence) })}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function KnowledgeList({ items }: { items: KnowledgeItem[] }) {
  const { t } = useTranslation();
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("employee:knowledge.emptyTitle")}</p>;
  }
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.id} className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{item.title}</p>
            <Badge
              variant={
                item.status === "active"
                  ? "success"
                  : item.status === "proposed"
                    ? "warning"
                    : "danger"
              }
            >
              {enumLabel(t, "employee:knowledge.status", item.status)}
            </Badge>
            {item.topic ? (
              <span className="ml-auto text-xs text-muted-foreground">{item.topic}</span>
            ) : null}
          </div>
          <p className="mt-1.5 line-clamp-3 text-sm text-muted-foreground">{item.content}</p>
        </li>
      ))}
    </ul>
  );
}
