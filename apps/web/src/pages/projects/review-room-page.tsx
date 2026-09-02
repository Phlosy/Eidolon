import { useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  FileText,
  MessageSquareText,
  MonitorPlay,
  ShieldCheck,
  UserRound,
  UsersRound,
  XCircle,
} from "lucide-react";
import { API_BASE_URL } from "../../api/client";
import { Button } from "../../components/common/button";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { Textarea } from "../../components/common/input";
import { useEmployees } from "../../hooks/useEmployees";
import { useDecideReview, useReview } from "../../hooks/useProjects";
import type { ReviewDecision } from "../../types";
import { cn } from "../../utils/cn";

const decisions: Array<{
  value: ReviewDecision;
  label: string;
  hint: string;
  tone: string;
}> = [
  { value: "approved", label: "Approve", hint: "批准并创建正式 Baseline", tone: "success" },
  {
    value: "conditionally_approved",
    label: "Approve with Conditions",
    hint: "批准，但保留必须跟进的行动项",
    tone: "warning",
  },
  {
    value: "changes_requested",
    label: "Request Changes",
    hint: "退回产出阶段并生成新文档版本",
    tone: "warning",
  },
  { value: "rejected", label: "Reject", hint: "阻塞当前 Gate", tone: "danger" },
];

export function ReviewRoomPage() {
  const { id, reviewId } = useParams<{ id: string; reviewId: string }>();
  const projectId = Number(id);
  const currentReviewId = Number(reviewId);
  const navigate = useNavigate();
  const reviewQuery = useReview(currentReviewId);
  const employees = useEmployees().data ?? [];
  const decide = useDecideReview(currentReviewId, projectId);
  const [decision, setDecision] = useState<ReviewDecision>("approved");
  const [comments, setComments] = useState("");
  const [actionItems, setActionItems] = useState("");
  const [error, setError] = useState("");
  const errorRef = useRef<HTMLDivElement>(null);

  const presenter = useMemo(
    () => employees.find((employee) => employee.id === reviewQuery.data?.presenter_employee_id),
    [employees, reviewQuery.data?.presenter_employee_id],
  );

  if (reviewQuery.isLoading)
    return <Skeleton className="h-[760px] rounded-[var(--radius-panel)]" />;
  if (reviewQuery.isError || !reviewQuery.data) {
    return <ErrorState error={reviewQuery.error} onRetry={() => reviewQuery.refetch()} />;
  }
  const review = reviewQuery.data;
  const packageDocuments = review.package?.documents ?? [];
  const documentUrl = (nodeId: number) => `${API_BASE_URL}/api/v1/drive/nodes/${nodeId}/content`;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (["changes_requested", "rejected"].includes(decision) && !comments.trim()) {
      setError("Request Changes 或 Reject 必须填写明确原因。 ");
      window.setTimeout(() => errorRef.current?.focus(), 0);
      return;
    }
    if (decision === "conditionally_approved" && !comments.trim() && !actionItems.trim()) {
      setError("条件批准必须填写条件或行动项。 ");
      window.setTimeout(() => errorRef.current?.focus(), 0);
      return;
    }
    decide.mutate(
      {
        decision,
        comments: comments.trim(),
        action_items: actionItems
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        expected_version: review.decision_version,
      },
      { onSuccess: () => navigate(`/projects/${projectId}`) },
    );
  };

  return (
    <div className="space-y-5 panel-enter">
      <section className="command-panel relative overflow-hidden border-warning/25 p-6 md:p-8">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,var(--status-waiting-glow),transparent_58%)]" />
        <div className="relative">
          <Link
            to={`/projects/${projectId}`}
            className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            返回项目指挥中心
          </Link>
          <div className="mt-8 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl">
              <div className="flex items-center gap-2 text-warning">
                <ShieldCheck className="h-4 w-4" />
                <p className="type-kicker">FORMAL REVIEW ROOM</p>
              </div>
              <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] md:text-5xl">
                {review.title}
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
                正式材料已按版本冻结。请核对文档与汇报材料，并给出会进入项目历史的客户决策。
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
              <div className="rounded-xl border border-border bg-background/45 p-3">
                <UserRound className="h-4 w-4 text-primary" />
                <p className="mt-3 text-[10px] text-muted-foreground">Presenter</p>
                <p className="mt-1 font-medium">{presenter?.name ?? "Unassigned"}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/45 p-3">
                <UsersRound className="h-4 w-4 text-primary" />
                <p className="mt-3 text-[10px] text-muted-foreground">Participants</p>
                <p className="mt-1 font-medium">
                  {Array.isArray(
                    (review.participants as { reviewer_names?: string[] }).reviewer_names,
                  )
                    ? (review.participants as { reviewer_names: string[] }).reviewer_names.length
                    : 0}
                </p>
              </div>
              <div className="col-span-2 rounded-xl border border-warning/25 bg-warning/7 p-3 sm:col-span-1">
                <MessageSquareText className="h-4 w-4 text-warning" />
                <p className="mt-3 text-[10px] text-muted-foreground">Status</p>
                <p className="mt-1 font-medium uppercase">{review.status.replaceAll("_", " ")}</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[1.1fr_.9fr]">
        <div className="space-y-5">
          <section className="command-panel p-5 md:p-6">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-primary" />
              <h2 className="text-base font-semibold">Review Package</h2>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {review.package?.title ?? "Materials are being prepared"}
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {packageDocuments.map((document) => (
                <article
                  key={document.id}
                  className="rounded-2xl border border-border bg-background/35 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="rounded-lg border border-border px-2 py-1 font-mono text-[9px] uppercase text-primary">
                      {document.format}
                    </span>
                    <span className="type-telemetry text-[9px] text-muted-foreground">
                      {document.version_label}
                    </span>
                  </div>
                  <h3 className="mt-4 text-sm font-medium">{document.title}</h3>
                  <p className="mt-1 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                    {document.category} · {document.document_type.replaceAll("_", " ")}
                  </p>
                  <a
                    href={`/drive?node=${document.drive_node_id}`}
                    className="mt-4 inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl border border-border text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {document.format === "pptx" ? (
                      <MonitorPlay className="h-3.5 w-3.5" />
                    ) : (
                      <Download className="h-3.5 w-3.5" />
                    )}
                    打开材料
                  </a>
                  <a
                    href={documentUrl(document.drive_node_id)}
                    download
                    className="mt-2 inline-flex w-full items-center justify-center gap-2 text-[11px] text-muted-foreground hover:text-foreground"
                  >
                    <Download className="h-3 w-3" />
                    下载原文件
                  </a>
                </article>
              ))}
            </div>
          </section>

          <section className="command-panel p-5 md:p-6">
            <h2 className="text-base font-semibold">Agenda</h2>
            <ol className="mt-4 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
              <li className="rounded-xl border border-border p-3">01 · Context & scope</li>
              <li className="rounded-xl border border-border p-3">02 · Key findings</li>
              <li className="rounded-xl border border-border p-3">03 · Open issues</li>
              <li className="rounded-xl border border-border p-3">04 · Customer decision</li>
            </ol>
          </section>
        </div>

        <form onSubmit={submit} className="command-panel h-fit p-5 md:sticky md:top-5 md:p-6">
          <p className="type-kicker text-warning">CUSTOMER DECISION</p>
          <h2 className="mt-2 text-xl font-semibold">记录评审结论</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            决定将创建 Baseline、触发返工，或阻塞当前 Gate。
          </p>
          {error || decide.isError ? (
            <div
              ref={errorRef}
              tabIndex={-1}
              role="alert"
              className="mt-4 rounded-xl border border-danger/25 bg-danger/7 p-3 text-xs outline-none focus:ring-2 focus:ring-danger/40"
            >
              {error ||
                (decide.error instanceof Error ? decide.error.message : "提交失败，请重试。")}
            </div>
          ) : null}
          <fieldset className="mt-5 space-y-2">
            <legend className="sr-only">Decision</legend>
            {decisions.map((item) => (
              <label
                key={item.value}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors",
                  decision === item.value
                    ? item.tone === "danger"
                      ? "border-danger/40 bg-danger/8"
                      : item.tone === "warning"
                        ? "border-warning/40 bg-warning/8"
                        : "border-success/40 bg-success/8"
                    : "border-border hover:bg-muted/40",
                )}
              >
                <input
                  type="radio"
                  name="decision"
                  value={item.value}
                  checked={decision === item.value}
                  onChange={() => {
                    setDecision(item.value);
                    setError("");
                  }}
                  className="mt-1"
                />
                <span>
                  <span className="block text-sm font-medium">{item.label}</span>
                  <span className="mt-0.5 block text-[10px] leading-4 text-muted-foreground">
                    {item.hint}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="mt-5 block space-y-1.5">
            <span className="text-xs font-medium">Review Comments</span>
            <Textarea
              value={comments}
              onChange={(event) => setComments(event.target.value)}
              placeholder="说明确认意见、条件或需要修改的内容…"
            />
          </label>
          <label className="mt-4 block space-y-1.5">
            <span className="text-xs font-medium">Action Items</span>
            <Textarea
              value={actionItems}
              onChange={(event) => setActionItems(event.target.value)}
              placeholder="每行一个行动项"
            />
          </label>
          <Button
            type="submit"
            className={cn("mt-5 w-full", decision === "rejected" && "bg-danger text-white")}
            disabled={decide.isPending || review.status === "completed"}
          >
            {decide.isPending ? (
              "正在记录正式决定…"
            ) : review.status === "completed" ? (
              "Review 已完成"
            ) : decision === "rejected" ? (
              <>
                <XCircle className="h-4 w-4" />
                提交 Reject
              </>
            ) : (
              <>
                <CheckCircle2 className="h-4 w-4" />
                提交正式决定
              </>
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}
