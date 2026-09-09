import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { usePracticePreview } from "../../hooks/useTutorial";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";

/**
 * 开始项目实战前的成本确认弹窗。
 *
 * 抽成独立组件是因为它有两个入口：教程中心的「开始实战」，以及核心教程通关后
 * 的交接卡片。两条路径都必须先让用户看到"谁用什么模型、会不会产生费用"，
 * 不能因为入口不同就跳过确认。
 */
export function PracticeDialog({
  open,
  onOpenChange,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStart: () => void;
}) {
  const { t } = useTranslation("tutorial");
  const preview = usePracticePreview(open);
  const data = preview.data;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("practice.dialogTitle")}
      description={t("practice.dialogBody")}
    >
      <div className="space-y-4">
        <p className="rounded-xl border border-primary/25 bg-primary/5 px-3 py-2 text-[11px] text-primary">
          {t("practice.accelerated")}
        </p>
        {data && data.team.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("practice.noEmployees")}</p>
        ) : null}
        {data ? (
          <>
            <table className="w-full text-[11px]">
              <caption className="mb-1 text-left text-[10px] text-muted-foreground">
                {t("practice.team")}
              </caption>
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-normal">{t("practice.runtime")}</th>
                  <th className="py-1 text-left font-normal">{t("practice.provider")}</th>
                  <th className="py-1 text-left font-normal">{t("practice.model")}</th>
                </tr>
              </thead>
              <tbody>
                {data.team.map((member) => (
                  <tr key={member.employee_id} data-tutorial-team-row={member.employee_id}>
                    <td className="py-1 font-mono">
                      {member.name} · {member.runtime ?? "—"}
                    </td>
                    <td className="py-1 font-mono">{member.provider ?? "—"}</td>
                    <td className="py-1 font-mono">{member.model ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p
              data-tutorial-cost={data.uses_llm ? "real" : "mock"}
              className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-[11px] ${
                data.uses_llm
                  ? "border-danger/40 bg-danger/5 text-danger"
                  : "border-success/35 bg-success/5 text-success"
              }`}
            >
              {data.uses_llm ? <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : null}
              <span>{data.uses_llm ? t("practice.usesLlm") : t("practice.mockOnly")}</span>
            </p>
            {data.uses_llm ? (
              <div className="text-[10px] leading-5 text-muted-foreground">
                <p>{t("practice.mockOption")}</p>
                <p>{t("practice.mockOptionHint")}</p>
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-xs text-muted-foreground">…</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("practice.notNow")}
          </Button>
          <Button
            onClick={() => {
              onOpenChange(false);
              onStart();
            }}
            disabled={!data}
            data-tutorial-action="start-practice"
          >
            {t("practice.start")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
