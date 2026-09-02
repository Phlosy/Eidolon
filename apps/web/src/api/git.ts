import { del, get, patch, post } from "./client";
import type {
  CreateGitConnectionInput,
  GitConnection,
  GitOverview,
  GitTestResult,
  UpdateGitConnectionInput,
} from "../types";

/** GET /git — builtin Gitea state + external connections. */
export function getGitOverview(): Promise<GitOverview> {
  return get<GitOverview>("/git");
}

export function createGitConnection(body: CreateGitConnectionInput): Promise<GitConnection> {
  return post<GitConnection>("/git/connections", body);
}

/** PATCH may include `token` to replace the stored credential (write-only). */
export function updateGitConnection(
  id: number,
  body: UpdateGitConnectionInput,
): Promise<GitConnection> {
  return patch<GitConnection>(`/git/connections/${id}`, body);
}

export function deleteGitConnection(id: number): Promise<void> {
  return del<void>(`/git/connections/${id}`);
}

export function testGitConnection(id: number): Promise<GitTestResult> {
  return post<GitTestResult>(`/git/connections/${id}/test`);
}

/** POST /git/builtin/install — kicks off the image pull + container create. */
export function installBuiltinGit(): Promise<{ status: "installing" }> {
  return post<{ status: "installing" }>("/git/builtin/install");
}

export function startBuiltinGit(): Promise<{ status: string }> {
  return post<{ status: string }>("/git/builtin/start");
}

export function stopBuiltinGit(): Promise<{ status: string }> {
  return post<{ status: string }>("/git/builtin/stop");
}
