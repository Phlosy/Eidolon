import {
  officeEmployeeStatuses,
  type OfficeBehaviorState,
  type OfficeEmployeeInput,
  type OfficeEmployeeState,
  type OfficeEmployeeStatus,
  type OfficeStateSnapshot,
} from "../types/office-state";

const statusSet = new Set<string>(officeEmployeeStatuses);

const statusBehavior: Record<OfficeEmployeeStatus, OfficeBehaviorState> = {
  WORKING: "WORKING",
  MEETING: "MEETING",
  RESEARCHING: "LEARNING",
  LEARNING: "LEARNING",
  REFLECTING: "INTERACTING",
  IDLE: "IDLE",
  OFFLINE: "OFFLINE",
  ERROR: "ERROR",
};

function normalizeStatus(status?: string | null): OfficeEmployeeStatus {
  const candidate = status?.toUpperCase() ?? "IDLE";
  return statusSet.has(candidate) ? (candidate as OfficeEmployeeStatus) : "IDLE";
}

export function behaviorForStatus(status: OfficeEmployeeStatus): OfficeBehaviorState {
  return statusBehavior[status];
}

export function adaptOfficeEmployee(input: OfficeEmployeeInput): OfficeEmployeeState {
  return {
    id: String(input.id),
    name: input.name,
    role: input.role ?? "Team member",
    department: input.department ?? "Shared Workspace",
    status: normalizeStatus(input.status),
    currentTask: input.currentTask ?? null,
    runtime: input.runtime ?? null,
    meetingId: input.meetingId ?? null,
    quote: input.quote ?? "正在整理今天的工作。",
    visualProfile: {
      skinId: input.visualProfile?.skinId ?? "employee-default",
      palette: input.visualProfile?.palette ?? "navy",
      outfit: input.visualProfile?.outfit,
      accessories: input.visualProfile?.accessories ?? [],
    },
  };
}

export function createOfficeSnapshot(
  employees: OfficeEmployeeInput[],
  options: { mode?: OfficeStateSnapshot["mode"]; revision?: number } = {},
): OfficeStateSnapshot {
  return {
    employees: employees.map(adaptOfficeEmployee),
    mode: options.mode ?? "live",
    revision: options.revision ?? Date.now(),
  };
}

export const authOfficeDemoSnapshot = createOfficeSnapshot(
  [
    {
      id: "demo-engineer",
      name: "林舟",
      role: "工程负责人",
      department: "Engineering",
      status: "WORKING",
      currentTask: "准备今天的构建发布",
      runtime: "Eidolon Runtime",
      quote: "构建通过了，准备今天的发布。",
      visualProfile: { skinId: "employee-engineer", palette: "navy" },
    },
    {
      id: "demo-operations",
      name: "苏禾",
      role: "运营负责人",
      department: "Operations",
      status: "IDLE",
      currentTask: "整理客户反馈",
      runtime: "Eidolon Runtime",
      quote: "客户反馈已整理，下午同步给团队。",
      visualProfile: { skinId: "employee-operations", palette: "clay" },
    },
    {
      id: "demo-project",
      name: "陈默",
      role: "项目负责人",
      department: "Product",
      status: "MEETING",
      currentTask: "主持需求评审",
      runtime: "Eidolon Runtime",
      meetingId: "demo-review",
      quote: "里程碑已拆好，十点开始评审。",
      visualProfile: { skinId: "employee-project", palette: "moss" },
    },
    {
      id: "demo-research",
      name: "闻溪",
      role: "研究员",
      department: "Research",
      status: "LEARNING",
      currentTask: "阅读模型评测报告",
      runtime: "Eidolon Runtime",
      quote: "我在比较新一轮评测结果。",
      visualProfile: { skinId: "employee-research", palette: "plum" },
    },
  ],
  { mode: "demo", revision: 1 },
);
