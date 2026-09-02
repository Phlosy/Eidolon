import { ArrowRight, Check, Circle, GraduationCap, SkipForward, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import {
  useCompleteTutorialStep,
  useDeferTutorialQa,
  useSkipTutorial,
  useTutorial,
} from "../../hooks/useTutorial";
import { Button } from "../common/button";
import { cn } from "../../utils/cn";

const steps = [
  ["company_setup", "Company Setup", "/"],
  ["hire_ceo", "Hire CEO", "/employees"],
  ["configure_ceo", "Configure CEO", "/employees"],
  ["hire_engineer", "Hire Engineer", "/employees"],
  ["hire_qa", "Role Coverage", "/employees"],
  ["create_project", "Create First Project", "/projects"],
  ["requirements_review", "Requirements Review", "/projects"],
  ["design_review", "Design Review", "/projects"],
  ["delivery", "Acceptance & Delivery", "/projects"],
] as const;

const explanations: Record<string, string> = {
  company_setup: "这是一家刚成立的 AI 公司。员工、项目和资产都从真实业务操作产生。",
  hire_ceo: "公司首先需要负责人。使用真实 Onboarding Wizard 招募第一位 CEO。",
  configure_ceo:
    "查看 CEO 的 Runtime、Provider、Workspace、Git、Documents、Memory 与 Learning。身份和经历会长期保留。",
  hire_engineer: "现在建立生产能力，为工程师配置独立 Runtime、Provider、Workspace 与权限包。",
  hire_qa: "独立 QA 能形成职责分离。你也可以稍后招聘，项目会保留 Role Coverage Warning。",
  create_project:
    "使用 Structured Project Intake 创建 Classic Snake，系统会生成 Project Charter 和完整 Phase。",
  requirements_review: "需求阶段不会自动越过用户 Gate。打开评审材料并记录正式决定。",
  design_review: "确认设计如何映射需求；批准后才会创建 Development Task Breakdown。",
  delivery: "完成测试、验收评审与 DeliveryPackage，查看所有 Baseline、变更和交付历史。",
};

export function TutorialGuide() {
  const location = useLocation();
  const tutorialQuery = useTutorial();
  const skip = useSkipTutorial();
  const complete = useCompleteTutorialStep();
  const deferQa = useDeferTutorialQa();
  const tutorial = tutorialQuery.data;
  const inReviewRoom = location.pathname.includes("/reviews/");
  if (!tutorial || tutorial.status !== "active" || inReviewRoom) return null;
  const current = steps.find(([key]) => key === tutorial.current_step) ?? steps[0];
  const currentIndex = steps.findIndex(([key]) => key === current[0]);

  return (
    <aside
      className={cn(
        "fixed inset-x-3 bottom-3 z-30 max-h-[70dvh] overflow-y-auto rounded-2xl border border-primary/25 bg-card/96 p-4 shadow-2xl backdrop-blur md:inset-x-auto md:bottom-5 md:right-5 md:w-[360px]",
      )}
      aria-label="Company tutorial"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <GraduationCap className="h-4 w-4" />
          </span>
          <div>
            <p className="type-kicker text-primary">GUIDED COMPANY TUTORIAL</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Step {currentIndex + 1} of {steps.length}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Skip tutorial"
          onClick={() => {
            if (window.confirm("跳过只会关闭引导，不会创建或删除员工与项目。确定跳过吗？"))
              skip.mutate();
          }}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ol className="mt-4 grid grid-cols-9 gap-1" aria-label="Tutorial progress">
        {steps.map(([key, label]) => {
          const done = tutorial.completed_steps.includes(key);
          const active = key === tutorial.current_step;
          return (
            <li
              key={key}
              title={label}
              className={cn(
                "flex h-1.5 rounded-full bg-muted",
                done && "bg-success",
                active && "bg-primary",
              )}
            >
              <span className="sr-only">
                {label}: {done ? "completed" : active ? "current" : "pending"}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="mt-5">
        <div className="flex items-center gap-2">
          {tutorial.completed_steps.includes(current[0]) ? (
            <Check className="h-4 w-4 text-success" />
          ) : (
            <Circle className="h-4 w-4 text-primary" />
          )}
          <h2 className="text-base font-semibold">{current[1]}</h2>
        </div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">{explanations[current[0]]}</p>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Link
          to={current[2]}
          className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-xs font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          前往操作
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        {current[0] === "configure_ceo" ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => complete.mutate("configure_ceo")}
          >
            我已了解
          </Button>
        ) : null}
        {current[0] === "hire_qa" ? (
          <Button type="button" size="sm" variant="outline" onClick={() => deferQa.mutate()}>
            <SkipForward className="h-3.5 w-3.5" />
            稍后招聘
          </Button>
        ) : null}
      </div>
    </aside>
  );
}
