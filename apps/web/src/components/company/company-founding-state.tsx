import { useState } from "react";
import { ArrowRight, Building2, FolderKanban, PackageOpen, Users } from "lucide-react";
import type { Company } from "../../types";
import { useStartTutorial } from "../../hooks/useTutorial";
import { Button } from "../common/button";
import { HireWizard } from "../lifecycle/hire-wizard";
import { useTranslation } from "react-i18next";

export function CompanyFoundingState({ company }: { company?: Company }) {
  const { t } = useTranslation("auth");
  const start = useStartTutorial();
  const [hireOpen, setHireOpen] = useState(false);
  const begin = () => start.mutate(undefined, { onSuccess: () => setHireOpen(true) });
  return (
    <section
      data-tutorial="company-overview"
      className="command-panel relative min-h-[620px] overflow-hidden p-6 md:p-10"
    >
      <div className="absolute -right-24 -top-24 h-80 w-80 rounded-full bg-primary/10 blur-3xl" />
      <div className="relative mx-auto flex max-w-4xl flex-col items-center py-12 text-center md:py-20">
        <span className="flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/25 bg-primary/10 text-primary">
          <Building2 className="h-7 w-7" />
        </span>
        <p className="type-kicker mt-8 text-primary">{t("founding.kicker")}</p>
        <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-[-0.04em] md:text-5xl">
          {t("founding.title", { company: company?.name ?? "Eidolon Studio" })}
        </h1>
        <p className="mt-5 max-w-2xl text-sm leading-6 text-muted-foreground">
          {t("founding.description")}
        </p>
        <div className="mt-8 grid w-full gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            [Building2, t("founding.companyStage"), "FOUNDING"],
            [Users, t("founding.employees"), "0"],
            [FolderKanban, t("founding.projects"), "0"],
            [PackageOpen, t("founding.assets"), "0"],
          ].map(([Icon, label, value]) => {
            const MetricIcon = Icon as typeof Building2;
            return (
              <div
                key={String(label)}
                className="rounded-2xl border border-border bg-background/40 p-4 text-left"
              >
                <MetricIcon className="h-4 w-4 text-primary" />
                <p className="mt-5 text-[10px] text-muted-foreground">{String(label)}</p>
                <p className="mt-1 text-sm font-semibold">{String(value)}</p>
              </div>
            );
          })}
        </div>
        <Button className="mt-8 min-w-56" onClick={begin} disabled={start.isPending}>
          {start.isPending ? t("founding.starting") : t("founding.start")}
          <ArrowRight className="h-4 w-4" />
        </Button>
        <p className="mt-3 text-[10px] text-muted-foreground">{t("founding.note")}</p>
      </div>
      <HireWizard open={hireOpen} onOpenChange={setHireOpen} presetRole="ceo" />
    </section>
  );
}
