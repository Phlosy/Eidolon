import { get, post } from "./client";
import type { CreateProjectInput, Project, ProjectDetail, ProjectGraph } from "../types";

export function listProjects(): Promise<Project[]> {
  return get<Project[]>("/projects");
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
