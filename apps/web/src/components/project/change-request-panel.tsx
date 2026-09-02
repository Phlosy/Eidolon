import { useState, type FormEvent } from "react";
import { GitPullRequestArrow, Plus, X } from "lucide-react";
import type { ProjectLifecycle } from "../../types";
import { useCreateChangeRequest } from "../../hooks/useProjects";
import { Button } from "../common/button";
import { Input, Textarea } from "../common/input";
import { Badge } from "../common/badge";

export function ChangeRequestPanel({ lifecycle }: { lifecycle: ProjectLifecycle }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [affected, setAffected] = useState<string[]>([]);
  const create = useCreateChangeRequest(lifecycle.project.id);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      {
        title,
        reason,
        requested_by: "Customer",
        priority: "medium",
        affected_requirements: affected,
      },
      {
        onSuccess: () => {
          setTitle("");
          setReason("");
          setAffected([]);
          setOpen(false);
        },
      },
    );
  };

  return (
    <section className="command-panel p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <GitPullRequestArrow className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Change Management</h2>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Baseline 通过后不允许直接覆盖；重大修改从 ChangeRequest 开始。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setOpen((value) => !value)}>
          {open ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          {open ? "取消" : "Create Change Request"}
        </Button>
      </div>
      {open ? (
        <form
          onSubmit={submit}
          className="mt-5 rounded-2xl border border-border bg-background/30 p-4"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5">
              <span className="text-xs font-medium">Change title *</span>
              <Input value={title} onChange={(event) => setTitle(event.target.value)} required />
            </label>
            <label className="space-y-1.5 sm:col-span-2">
              <span className="text-xs font-medium">Reason *</span>
              <Textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                required
              />
            </label>
          </div>
          <fieldset className="mt-4">
            <legend className="text-xs font-medium">Affected Requirements</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {lifecycle.requirements.map((requirement) => (
                <label
                  key={requirement.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg border border-border px-2.5 py-2 text-xs hover:bg-muted"
                >
                  <input
                    type="checkbox"
                    checked={affected.includes(requirement.code)}
                    onChange={() =>
                      setAffected((items) =>
                        items.includes(requirement.code)
                          ? items.filter((item) => item !== requirement.code)
                          : [...items, requirement.code],
                      )
                    }
                  />
                  {requirement.code}
                </label>
              ))}
            </div>
          </fieldset>
          {create.isError ? (
            <p role="alert" className="mt-3 text-xs text-danger">
              {create.error instanceof Error ? create.error.message : "创建失败"}
            </p>
          ) : null}
          <div className="mt-4 flex justify-end">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "正在创建…" : "开始影响分析"}
            </Button>
          </div>
        </form>
      ) : null}
      <div className="mt-5 space-y-2">
        {lifecycle.change_requests.length ? (
          lifecycle.change_requests.map((change) => (
            <article
              key={change.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-border px-4 py-3"
            >
              <span className="type-telemetry text-xs text-primary">{change.code}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{change.title}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{change.reason}</p>
              </div>
              <Badge variant="muted">{change.status.replaceAll("_", " ")}</Badge>
            </article>
          ))
        ) : (
          <p className="rounded-xl border border-dashed border-border px-4 py-5 text-center text-xs text-muted-foreground">
            当前没有 ChangeRequest。
          </p>
        )}
      </div>
    </section>
  );
}
