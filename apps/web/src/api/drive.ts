import { API_BASE_URL, ApiError, authHeaders, get, patch, post } from "./client";
import type {
  CreateDriveFolderInput,
  DriveNode,
  DriveNodeDetail,
  DriveRevision,
  DriveZone,
  UploadDriveFileInput,
  UpdateDriveNodeInput,
} from "../types";

/** Flat node list for the whole drive or a single zone; the tree is built client-side. */
export function listDriveTree(zone?: DriveZone): Promise<DriveNode[]> {
  return get<DriveNode[]>(`/drive/tree${zone ? `?zone=${zone}` : ""}`);
}

export function getDriveNode(id: number): Promise<DriveNodeDetail> {
  return get<DriveNodeDetail>(`/drive/nodes/${id}`);
}

export function getDriveRevisions(id: number): Promise<DriveRevision[]> {
  return get<DriveRevision[]>(`/drive/nodes/${id}/revisions`);
}

async function rawDriveRequest(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1${path}`, {
      ...init,
      // 原始请求也要带会话与 CSRF：这里是 Blob / 文件上传，用不了 client.ts 的
      // request()，但鉴权头必须同源，否则写操作全部 403。
      credentials: "include",
      headers: { ...authHeaders(), ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Eidolon API");
  }
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Preserve the generic status message for non-JSON responses.
    }
    throw new ApiError(response.status, detail);
  }
  return response;
}

export async function getDriveNodeContent(id: number): Promise<Blob> {
  return (await rawDriveRequest(`/drive/nodes/${id}/content`)).blob();
}

export async function uploadDriveFile(input: UploadDriveFileInput): Promise<DriveNode> {
  const params = new URLSearchParams({ zone: input.zone, name: input.file.name });
  if (input.parent_id != null) params.set("parent_id", String(input.parent_id));
  if (input.project_id != null) params.set("project_id", String(input.project_id));
  const response = await rawDriveRequest(`/drive/files?${params}`, {
    method: "POST",
    headers: { "Content-Type": input.file.type || "application/octet-stream" },
    body: input.file,
  });
  return (await response.json()) as DriveNode;
}

/** Edit a document's content; the backend creates a new revision (message optional). */
export function updateDriveNode(id: number, body: UpdateDriveNodeInput): Promise<DriveNode> {
  return patch<DriveNode>(`/drive/nodes/${id}`, body);
}

export function createDriveFolder(body: CreateDriveFolderInput): Promise<DriveNode> {
  return post<DriveNode>("/drive/folders", body);
}
