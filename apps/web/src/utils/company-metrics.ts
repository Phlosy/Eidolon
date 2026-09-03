import type { TaskStatus } from "../types";

export function deriveCompanyProgress(input: {
  employees: number;
  completedProjects: number;
  documents: number;
  completedTasks: number;
}) {
  const xp =
    input.employees * 50 +
    input.completedProjects * 60 +
    input.documents * 2 +
    input.completedTasks * 3;
  const level = Math.floor(xp / 200) + 2;
  const currentLevelXp = xp % 200;
  const nextLevelXp = 200;
  return {
    level,
    xp,
    currentLevelXp,
    nextLevelXp,
    percent: Math.round((currentLevelXp / nextLevelXp) * 100),
  };
}

export function deriveProjectProgress(tasks: Array<{ status: TaskStatus | string }>) {
  const completed = tasks.filter((task) => task.status === "done").length;
  const total = tasks.length;
  return { completed, total, percent: total === 0 ? 0 : Math.round((completed / total) * 100) };
}
