import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Report, ReportLine } from "../../api/types";
import { TraceDialog } from "./trace-dialog";
import { Mono } from "./widgets";

/**
 * A narrative report (R7-b): sections of plain sentences, each carrying the fact ids
 * it was folded from. Click a fact id to open its causal trace; a line that names a
 * run links to it. Nothing here is stored — it is the log, read aloud.
 */
export function ReportView({ report }: { report: Report }) {
  const { t } = useTranslation("app");
  const [trace, setTrace] = useState<string | null>(null);
  const total = report.sections.reduce(
    (n, s) => n + s.lines.reduce((m, ln) => m + ln.facts.length, 0),
    0,
  );
  return (
    <div className="space-y-5" data-testid="report">
      <p className="text-xs text-muted-foreground">
        {t("projects.reportHint")} · {t("projects.reportFacts", { count: total })}
      </p>
      {report.sections.map((section) => (
        <section key={section.key}>
          <h3 className="text-sm font-semibold">{section.title}</h3>
          <ul className="mt-2 space-y-2">
            {section.lines.map((line, i) => (
              <ReportLineView key={i} line={line} onTrace={setTrace} />
            ))}
          </ul>
        </section>
      ))}
      <TraceDialog factId={trace} onClose={() => setTrace(null)} />
    </div>
  );
}

function ReportLineView({
  line,
  onTrace,
}: {
  line: ReportLine;
  onTrace: (factId: string) => void;
}) {
  const runLink = line.link?.kind === "run" ? `/runs/${line.link.id}` : null;
  return (
    <li className="rounded-lg border border-border px-3 py-2 text-sm">
      <p>
        {runLink ? (
          <Link to={runLink} className="hover:underline">
            {line.text}
          </Link>
        ) : (
          line.text
        )}
      </p>
      {line.facts.length ? (
        <p className="mt-1 flex flex-wrap gap-1">
          {line.facts.slice(0, 12).map((f) => (
            <button
              key={f}
              type="button"
              className="rounded border border-border bg-muted/40 px-1.5 py-0.5 hover:bg-muted"
              onClick={() => onTrace(f)}
              aria-label={`trace ${f}`}
            >
              <Mono>{f.slice(0, 8)}</Mono>
            </button>
          ))}
          {line.facts.length > 12 ? (
            <Mono className="text-muted-foreground">+{line.facts.length - 12}</Mono>
          ) : null}
        </p>
      ) : null}
    </li>
  );
}
