import { apiClient } from '@/services/api/client';

export interface Window {
  remainingPercent: number | null;
  resetsAt: number | null;
  windowDurationMins: number | null;
}
export interface Account {
  alias: string;
  eligible: boolean;
  reason: string;
  ageSeconds: number | null;
  windows: Window[];
}
export interface Project {
  id: string;
  name: string;
  path: string;
  writable: boolean;
}
export interface Task {
  id: string;
  title: string;
  project: string;
  state: string;
  account: string;
  model: string;
  session: string;
  workspace: string;
  branch: string;
  route: string;
  error: string;
  updated: number;
  goal: string;
  result: string;
  usage: string;
}
export interface Operation {
  task: string;
  item: string;
  kind: string;
  command: string;
  state: string;
  exitCode: number | null;
  started: number;
  finished: number | null;
}
export interface Event {
  id: number;
  task: string;
  at: number;
  kind: string;
  message: string;
}
export interface Choice {
  account: string;
  eligible: boolean;
  reason: string;
  reset: number | null;
}
export interface FleetStatus {
  now: number;
  started: number;
  host: string;
  dispatchEnabled: boolean;
  activeSlots: number;
  maxSlots: number;
  reservePercent: number;
  nextAccount: string;
  nextReason: string;
  accounts: Account[];
  projects: Project[];
  tasks: Task[];
  operations: Operation[];
  events: Event[];
  choices: Choice[];
  gatewayConfigured: boolean;
  gatewayRequests: number | null;
  gatewaySuccess: number | null;
  gatewayFailed: number | null;
}

const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const text = (value: unknown): string => (typeof value === 'string' ? value : '');
const number = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

export const decodeTask = (value: unknown): Task => {
  const entry = object(value);
  return {
    id: text(entry.id),
    title: text(entry.title),
    project: text(entry.project),
    state: text(entry.state),
    account: text(entry.account),
    model: text(entry.model),
    session: text(entry.session),
    workspace: text(entry.workspace),
    branch: text(entry.branch),
    route: text(entry.route),
    error: text(entry.error),
    updated: number(entry.updated) ?? 0,
    goal: text(entry.goal),
    result: text(entry.result),
    usage: text(entry.usage),
  };
};
export const decodeOperation = (value: unknown): Operation => {
  const entry = object(value);
  return {
    task: text(entry.task),
    item: text(entry.item),
    kind: text(entry.kind),
    command: text(entry.command),
    state: text(entry.state),
    exitCode: number(entry.exit_code),
    started: number(entry.started) ?? 0,
    finished: number(entry.finished),
  };
};
export function decodeStatus(value: unknown): FleetStatus {
  const root = object(value);
  if (root.name !== 'ranton fleet' || !Array.isArray(root.tasks)) {
    throw new Error('The fleet coordinator returned an incompatible response');
  }
  const routing = object(root.routing);
  const gateway = object(root.gateway);
  return {
    now: number(root.now) ?? 0,
    started: number(root.started) ?? 0,
    host: text(root.host),
    dispatchEnabled: root.dispatchEnabled === true,
    activeSlots: number(root.activeSlots) ?? 0,
    maxSlots: number(root.maxSlots) ?? 0,
    reservePercent: number(root.reservePercent) ?? 0,
    nextAccount: text(root.nextAccount),
    nextReason: text(root.nextReason),
    gatewayConfigured: routing.gatewayConfigured === true,
    gatewayRequests: number(gateway.requests),
    gatewaySuccess: number(gateway.success),
    gatewayFailed: number(gateway.failed),
    accounts: list(root.accounts).map((value) => {
      const entry = object(value);
      const windows = list(entry.quotaWindows).flatMap((bucket) => {
        const value = object(bucket);
        return [value.primary, value.secondary].filter(Boolean).map((window) => {
          const fields = object(window);
          return {
            remainingPercent: number(fields.remainingPercent),
            resetsAt: number(fields.resetsAt),
            windowDurationMins: number(fields.windowDurationMins),
          };
        });
      });
      return {
        alias: text(entry.alias),
        eligible: entry.eligible === true,
        reason: text(entry.reason),
        ageSeconds: number(entry.ageSeconds),
        windows,
      };
    }),
    projects: list(root.projects).map((value) => {
      const entry = object(value);
      return {
        id: text(entry.id),
        name: text(entry.name),
        path: text(entry.path),
        writable: entry.writable === 1 || entry.writable === true,
      };
    }),
    tasks: list(root.tasks).map(decodeTask),
    operations: list(root.operations).map(decodeOperation),
    events: list(root.events).map((value) => {
      const entry = object(value);
      return {
        id: number(entry.id) ?? 0,
        task: text(entry.task),
        at: number(entry.at) ?? 0,
        kind: text(entry.kind),
        message: text(entry.message),
      };
    }),
    choices: list(routing.choices).map((value) => {
      const entry = object(value);
      return {
        account: text(entry.account),
        eligible: entry.eligible === true,
        reason: text(entry.reason),
        reset: number(entry.reset),
      };
    }),
  };
}

export const fleetApi = {
  status: async () => decodeStatus(await apiClient.get('/fleet/status')),
  detail: async (id: string) => {
    const payload = object(await apiClient.get('/fleet/tasks/' + encodeURIComponent(id)));
    return {
      task: decodeTask(payload.task),
      operations: list(payload.operations).map(decodeOperation),
    };
  },
  action: (
    id: string,
    action: 'pause' | 'resume' | 'complete' | 'followup',
    data: unknown = {}
  ) => {
    if (!['pause', 'resume', 'complete', 'followup'].includes(action)) {
      throw new Error('Unknown fleet action');
    }
    return apiClient.post('/fleet/tasks/' + encodeURIComponent(id) + '/' + action, data);
  },
  submit: async (data: unknown) => decodeTask(await apiClient.post('/fleet/tasks', data)),
  project: (data: unknown) => apiClient.post('/fleet/projects', data),
  dispatch: (enabled: boolean) => apiClient.post('/fleet/dispatch', { enabled }),
  refresh: () => apiClient.post('/fleet/refresh', {}),
  policy: (reservePercent: number, maxSlots: number) =>
    apiClient.post('/fleet/policy', { reservePercent, maxSlots }),
};

export function orderTasks(tasks: Task[]): Task[] {
  const ranks: Record<string, number> = {
    needs_input: 0,
    review: 1,
    running: 2,
    waiting: 3,
    queued: 4,
    paused: 5,
    completed: 6,
  };
  return [...tasks].sort(
    (a, b) => (ranks[a.state] ?? 7) - (ranks[b.state] ?? 7) || b.updated - a.updated
  );
}
