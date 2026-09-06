import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BehaviorPreview } from "./behavior-preview";
import type { BehaviorPolicySummary } from "../../types";

function makePolicy(overrides: Partial<BehaviorPolicySummary> = {}): BehaviorPolicySummary {
  return {
    policy_version: "behavior-v1",
    profile_revision: 1234,
    band: "high",
    traits: { curiosity: 0.9 },
    work_directives: ["主动探索"],
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
    ...overrides,
  };
}

function jsonResponse(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } }),
  );
}

function renderPreview(curiosity: number, learningEnabled = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BehaviorPreview curiosity={curiosity} learningEnabled={learningEnabled} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BehaviorPreview", () => {
  it("renders the server-computed band and quotas instead of guessing them locally", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      jsonResponse(
        url.includes("value=0.1")
          ? makePolicy({
              band: "low",
              traits: { curiosity: 0.1 },
              work_directives: ["聚焦交付"],
              retrieval: {
                knowledge_limit: 2,
                include_candidate_skills: false,
                candidate_min_success_rate: 0.7,
                novel_topic_ratio: 0.04,
                max_context_items: 5,
              },
              reflection: {
                open_question_count: 0,
                alternative_hypotheses: 0,
                note_style: "standard",
              },
              learning: {
                followup_topics_per_task: 0,
                followup_priority_score: 34,
                priority_score_cap: 69,
                topic_source: "kind_map",
              },
            })
          : makePolicy(),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const view = renderPreview(0.9);
    await waitFor(() => expect(screen.getByText("high")).toBeInTheDocument());
    expect(screen.getByText("Adjacent knowledge")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // 学习关闭时说明文案要跟着变（避免用户以为额度仍然生效）
    expect(screen.queryByText(/Learning is off/)).not.toBeInTheDocument();
    view.unmount();

    renderPreview(0.1);
    await waitFor(() => expect(screen.getByText("low")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("trait=curiosity") && url.includes("value=0.1"))).toBe(
      true,
    );
  });

  it("tells the user that learning off falls back to the default working style", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        jsonResponse(
          makePolicy({
            band: "moderate",
            work_directives: [],
            retrieval: {
              knowledge_limit: 5,
              include_candidate_skills: false,
              candidate_min_success_rate: 0.7,
              novel_topic_ratio: 0,
              max_context_items: 8,
            },
            reflection: {
              open_question_count: 0,
              alternative_hypotheses: 0,
              note_style: "standard",
            },
            learning: {
              followup_topics_per_task: 0,
              followup_priority_score: 0,
              priority_score_cap: 69,
              topic_source: "kind_map",
            },
          }),
        ),
      ),
    );
    renderPreview(0.9, false);
    await waitFor(() => expect(screen.getByText(/Learning is off/)).toBeInTheDocument());
  });
});
