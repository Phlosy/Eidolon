import { get, patch } from "./client";
import type { Task, TaskStatus } from "../types";

export function getTask(id: number): Promise<Task> {
  return get<Task>(`/tasks/${id}`);
}

export function updateTaskStatus(id: number, status: TaskStatus): Promise<Task> {
  return patch<Task>(`/tasks/${id}`, { status });
}
