export const officeEmployeeStatuses = [
  "WORKING",
  "MEETING",
  "RESEARCHING",
  "LEARNING",
  "REFLECTING",
  "IDLE",
  "OFFLINE",
  "ERROR",
] as const;

export type OfficeEmployeeStatus = (typeof officeEmployeeStatuses)[number];

export type OfficeBehaviorState =
  | "SPAWN"
  | "IDLE"
  | "MOVING"
  | "WORKING"
  | "MEETING"
  | "LEARNING"
  | "INTERACTING"
  | "LEAVING"
  | "OFFLINE"
  | "ERROR";

export type OfficeVisualProfile = {
  skinId: string;
  palette: "navy" | "clay" | "moss" | "plum";
  outfit?: string;
  accessories?: string[];
};

export type OfficeEmployeeState = {
  id: string;
  name: string;
  role: string;
  department: string;
  status: OfficeEmployeeStatus;
  currentTask: string | null;
  runtime: string | null;
  meetingId: string | null;
  quote: string;
  visualProfile: OfficeVisualProfile;
};

export type OfficeStateSnapshot = {
  employees: OfficeEmployeeState[];
  mode: "demo" | "live";
  revision: number;
};

export type OfficeEmployeeInput = Omit<
  Partial<OfficeEmployeeState>,
  "id" | "name" | "status" | "visualProfile"
> & {
  id: string | number;
  name: string;
  status?: string | null;
  visualProfile?: Partial<OfficeVisualProfile> | null;
};
