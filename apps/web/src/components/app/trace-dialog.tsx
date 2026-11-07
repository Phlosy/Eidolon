import { useTranslation } from "react-i18next";
import { Dialog } from "../common/dialog";
import { ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { useTrace } from "../../hooks/queries";
import { formatDateTime } from "../../utils/format";
import { Mono } from "./widgets";

/** "Why does this fact exist?": the causal ancestry the kernel records. */
export function TraceDialog({ factId, onClose }: { factId: string | null; onClose: () => void }) {
  const { t } = useTranslation("app");
  const query = useTrace(factId);
  return (
    <Dialog
      open={Boolean(factId)}
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={t("run.trace")}
      description={factId ?? undefined}
      className="max-w-2xl"
    >
      {query.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      {query.data ? (
        <ol className="scroll-area max-h-[60vh] space-y-2 overflow-auto pr-1 text-xs">
          {query.data.facts.map((f) => (
            <li
              key={f.fact_id}
              className={
                f.fact_id === query.data.root
                  ? "rounded-xl border border-primary/40 bg-primary/5 p-3"
                  : "rounded-xl border border-border p-3"
              }
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">{f.kind}</span>
                <span className="text-muted-foreground">{formatDateTime(f.created_at)}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-muted-foreground">
                <span>
                  seq {f.seq} · {f.actor.kind}
                  {f.actor.id ? `:${f.actor.id}` : ""}
                </span>
                <Mono>{f.fact_id}</Mono>
              </div>
              <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-muted/40 p-2 font-mono text-[11px]">
                {JSON.stringify(f.payload, null, 2)}
              </pre>
            </li>
          ))}
          {query.data.missing.length ? (
            <li className="text-danger">missing: {query.data.missing.join(", ")}</li>
          ) : null}
        </ol>
      ) : null}
    </Dialog>
  );
}
