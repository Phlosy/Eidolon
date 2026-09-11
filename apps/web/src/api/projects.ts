import { get, post } from "./client";
import type {
  CreateProjectInput,
  Project,
  ProjectDetail,
  ProjectGraph,
  ProjectTimeline,
  ProjectLifecycle,
  ProjectSpec,
  ReviewDecisionInput,
  ReviewMeeting,
  ChangeRequest,
} from "../types";

export function listProjects(): Promise<Project[]> {
  return get<Project[]>("/projects");
}

export function getProjectPortfolio(): Promise<ProjectTimeline[]> {
  return get<ProjectTimeline[]>("/projects/portfolio");
}

/** Creating a project = placing a customer order (§8). */
export function createProject(input: CreateProjectInput): Promise<Project> {
  return post<Project>("/projects", input);
}

export function getProject(id: number): Promise<ProjectDetail> {
  return get<ProjectDetail>(`/projects/${id}`);
}

export function getProjectGraph(id: number): Promise<ProjectGraph> {
  return get<ProjectGraph>(`/projects/${id}/graph`);
}

export function getProjectLifecycle(id: number): Promise<ProjectLifecycle> {
  return get<ProjectLifecycle>(`/projects/${id}/lifecycle`);
}

/**
 * M2.1 Canonical Executable Project 读面：回答那 8 个问题
 * （spec / work_mode / Work Intake 责任 / 承担它的任职 / 管理 actor /
 *  requirements+deliverables+acceptance / spec 版本 / 是否已进入执行）。
 */
export function getProjectSpec(id: number): Promise<ProjectSpec> {
  return get<ProjectSpec>(`/projects/${id}/spec`);
}

export function completeProjectPhase(
  projectId: number,
  phaseId: number,
): Promise<ProjectLifecycle> {
  return post<ProjectLifecycle>(`/projects/${projectId}/phases/${phaseId}/complete`);
}

export function getReview(reviewId: number): Promise<ReviewMeeting> {
  return get<ReviewMeeting>(`/reviews/${reviewId}`);
}

export function decideReview(
  reviewId: number,
  input: ReviewDecisionInput,
): Promise<ProjectLifecycle> {
  return post<ProjectLifecycle>(`/reviews/${reviewId}/decision`, input);
}

export function createChangeRequest(
  projectId: number,
  input: {
    title: string;
    reason: string;
    requested_by: string;
    priority: string;
    affected_requirements: string[];
  },
): Promise<ChangeRequest> {
  return post<ChangeRequest>(`/projects/${projectId}/change-requests`, input);
}
