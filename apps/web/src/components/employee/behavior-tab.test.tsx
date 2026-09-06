import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BehaviorTab } from "./behavior-tab";
import type {
  BehaviorPolicySummary,
  BehaviorProjection,
  EmployeeBrain,
  SkillUsageBenchmarks,
} from "../../types";

const POLICY: BehaviorPolicySummary = {
  policy_version: "behavior-v1",
  profile_revision: 4242,
  band: "high",
  traits: { curiosity: 0.9 },
  work_directives: ["主动探索：不要停在第一个解释。"],
  retrieval: {
    knowledge_limit: 8,
    include_candidate_skills: true,
    candidate_min_success_rate: 0.7,
    novel_topic_ratio: 0.36,
    max_context_items: 11,
  },
  reflection: { open_question_count: 3, alternative_hypotheses: 2, note_style: "exploratory" },
  learning: {
    followup_topics_per_task: 2,
    followup_priority_score: 66,
    priority_score_cap: 69,
    topic_source: "kind_map+interest",
  },
};

const BRAIN: EmployeeBrain = {
  employee_id: 7,
  personality: "",
  goals: "",
  interests: [],
  learning_policy: { enabled: true },
  memory_policy: {},
  curiosity: 0.9,
  traits: { schema_version: 1, curiosity: 0.9 },
  behavior: POLICY,
};

const PROJECTION: BehaviorProjection = {
  employee_id: 7,
  policy_version: "behavior-v1",
  revision: 4242,
  band: "high",
  projection_markdown: "# 大脑\n",
  paths: ["/data/employees/7/brain/PROFILE.md", "/data/employees/7/brain/eidolon/behavior.md"],
  mirrored_revision: null,
  mirror_current: false,
};

const BENCHMARKS: SkillUsageBenchmarks = {
  total_usages: 10,
  candidate_usages: 4,
  candidate_skills_tried: 2,
  rated_usages: 1,
  pending_ratings: 5,
  trial_rate: 0.4,
  conversion_rate: 0.75,
  useful_rate: 1,
};

function json(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } }),
  );
}

function renderTab(benchmarks: SkillUsageBenchmarks = BENCHMARKS) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/brain")) return json(BRAIN);
    if (url.includes("/brain/projection")) return json(PROJECTION);
    if (url.includes("/skill-usages/benchmarks")) return json(benchmarks);
    throw new Error(`unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BehaviorTab employeeId={7} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BehaviorTab", () => {
  it("shows the effective quotas, the benchmark rates and the projection state", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("Adjacent knowledge")).toBeInTheDocument());
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText(/不要停在第一个解释/)).toBeInTheDocument();
    // 指标：0.4 → 40%，且明确区分"待评价"
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // pending 单独一个数字，避免与其它额度混淆
    // 投影修订 + 容器未同步要诚实展示（不能假装生效）
    expect(screen.getByText("4242")).toBeInTheDocument();
    expect(screen.getByText(/next recreate/)).toBeInTheDocument();
  });

  it("renders an em dash for rates without data instead of a fake 0%", async () => {
    renderTab({
      total_usages: 0,
      candidate_usages: 0,
      candidate_skills_tried: 0,
      rated_usages: 0,
      pending_ratings: 0,
      trial_rate: null,
      conversion_rate: null,
      useful_rate: null,
    });
    await waitFor(() => expect(screen.getByText("Candidate skill benchmarks")).toBeInTheDocument());
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBe(3); // trial / conversion / useful
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("surfaces a missing policy instead of rendering an empty card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => json({ ...BRAIN, behavior: null })),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <BehaviorTab employeeId={7} />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(screen.getByText("No behaviour policy available.")).toBeInTheDocument(),
    );
  });
});
