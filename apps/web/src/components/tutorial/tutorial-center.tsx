import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BookOpen, CircleHelp } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { getTutorialCenter } from "../../api/tutorial";

export function TutorialCenter() {
  const { t, i18n } = useTranslation("auth");
  const query = useQuery({ queryKey: ["tutorial", "center"], queryFn: getTutorialCenter });
  return (
    <section id="tutorial-center" className="command-panel p-5 md:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="type-kicker text-primary">{t("tutorial.center.kicker")}</p>
          <h2 className="mt-2 flex items-center gap-2 text-xl font-semibold">
            <BookOpen className="h-5 w-5 text-primary" />
            {t("tutorial.center.title")}
          </h2>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {t("tutorial.center.description")}
          </p>
        </div>
        <CircleHelp className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="mt-5 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {query.data?.map((chapter) => (
          <Link
            key={chapter.id}
            to={chapter.route}
            className="group flex min-h-16 items-center justify-between rounded-xl border border-border bg-background/35 px-4 text-xs font-medium transition hover:border-border-active hover:bg-surface-interactive"
          >
            <span>{chapter.title[i18n.language.startsWith("en") ? "en-US" : "zh-CN"]}</span>
            <ArrowUpRight className="h-4 w-4 text-muted-foreground transition group-hover:text-primary" />
          </Link>
        ))}
      </div>
    </section>
  );
}
