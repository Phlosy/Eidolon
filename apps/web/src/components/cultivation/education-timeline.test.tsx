import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EducationTimeline } from "./education-timeline";
import type { EducationEvent } from "../../api/cultivation";

const STAGE_EVENT: EducationEvent = {
  id: 1,
  program_id: 1,
  kind: "exam",
  topic: "高中",
  outcome: {
    stage_id: "senior",
    topics: ["函数与方程", "力学"],
    signals: [68, 74],
    knowledge_produced: 5,
    duration_weeks: 48,
    assessment_run_id: 7,
  },
  evidence_id: 3,
  occurred_at: "2026-09-01T00:00:00Z",
};

const FORTUNE_EVENT: EducationEvent = {
  id: 2,
  program_id: 1,
  kind: "fortune",
  topic: "高中",
  outcome: {
    fortune: "competition_win",
    narrative: "在学科竞赛中获奖",
    signal_delta: 10,
    extra_topics: ["竞赛集训"],
    trait_shift: { conscientiousness: 0.03 },
  },
  evidence_id: null,
  occurred_at: "2026-09-02T00:00:00Z",
};

describe("EducationTimeline", () => {
  it("renders the empty hint before any event exists", () => {
    render(<EducationTimeline events={[]} />);
    expect(
      screen.getByText("No record yet — advance a stage or run a free cultivation session."),
    ).toBeInTheDocument();
  });

  it("shows covered topics with their evidence signals and the assessment marker", () => {
    render(<EducationTimeline events={[STAGE_EVENT]} />);
    expect(screen.getByText("Exam")).toBeInTheDocument();
    // 阶段标题走 stage_id 的 i18n（模板注册表里是中文数据）
    expect(screen.getByText("High school")).toBeInTheDocument();
    expect(screen.getByText(/函数与方程/)).toBeInTheDocument();
    expect(screen.getByText("68")).toBeInTheDocument();
    expect(screen.getByText("5 knowledge items")).toBeInTheDocument();
    expect(screen.getByText("48 weeks")).toBeInTheDocument();
    expect(screen.getByText("Stage assessment completed")).toBeInTheDocument();
  });

  it("renders fortune events as narrative with signal and trait shifts", () => {
    render(<EducationTimeline events={[FORTUNE_EVENT]} />);
    expect(screen.getByText("Fortune")).toBeInTheDocument();
    expect(screen.getByText("在学科竞赛中获奖")).toBeInTheDocument();
    expect(screen.getByText("Evidence signal +10")).toBeInTheDocument();
    expect(screen.getByText(/Extra topics: 竞赛集训/)).toBeInTheDocument();
    expect(screen.getByText(/Conscientiousness \+0.03/)).toBeInTheDocument();
  });

  it("orders events newest first", () => {
    render(<EducationTimeline events={[STAGE_EVENT, FORTUNE_EVENT]} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("在学科竞赛中获奖");
    expect(items[1]).toHaveTextContent("Stage assessment completed");
  });
});
