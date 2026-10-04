import type { Event, Operation, Project, Task } from './api';

export const accountName = (name: string) =>
  name ? name.charAt(0).toUpperCase() + name.slice(1) : '—';
export const stamp = (value: number | null) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(value * 1000))
    : '—';
export const controlChatLink = (id: string): string | null =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)
    ? 'codex://threads/' + id
    : null;

export function resultPreview(value: string): string {
  const blocks = value.split(/\n\s*\n/).filter((block) => {
    const text = block.trim();
    return text && !/^#{1,6}\s/.test(text) && !(text.length < 100 && /^[^\n]+:$/.test(text));
  });
  const preview = blocks.slice(0, 2).join('\n\n') || value;
  return preview.length > 700 ? preview.slice(0, 697).replace(/\s+\S*$/, '') + '…' : preview;
}

export function matchesTask(task: Task, project: string, filter: string, query: string): boolean {
  const states: Record<string, string[]> = {
    open: ['needs_input', 'review', 'running', 'waiting', 'queued', 'paused'],
    running: ['running'],
    results: ['review', 'needs_input'],
    done: ['completed'],
  };
  return (
    (!project || task.project === project) &&
    (!states[filter] || states[filter].includes(task.state)) &&
    [task.title, task.originChatTitle, task.model, task.account]
      .join(' ')
      .toLowerCase()
      .includes(query.trim().toLowerCase())
  );
}

export function projectGroups(projects: Project[], tasks: Task[]) {
  const known = new Set(projects.map((project) => project.id));
  return [
    ...projects.map((project) => ({
      project,
      tasks: tasks.filter((task) => task.project === project.id),
    })),
    ...(!tasks.some((task) => !known.has(task.project))
      ? []
      : [
          {
            project: null,
            tasks: tasks.filter((task) => !known.has(task.project)),
          },
        ]),
  ].filter((group) => group.tasks.length);
}

export interface ActivityItem {
  id: string;
  at: number;
  task: Task | null;
  operation?: Operation;
  event?: Event;
}
export function activityItems(
  operations: Operation[],
  events: Event[],
  tasks: Task[],
  project: string
) {
  const index = new Map(tasks.map((task) => [task.id, task]));
  const entries: ActivityItem[] = [
    ...operations.map((operation) => ({
      id: 'op:' + operation.task + ':' + operation.item,
      at: operation.started,
      task: index.get(operation.task) ?? null,
      operation,
    })),
    ...events
      .filter((event) => event.kind !== 'gateway_route' && event.kind !== 'progress')
      .map((event) => ({
        id: 'event:' + event.id,
        at: event.at,
        task: index.get(event.task) ?? null,
        event,
      })),
  ];
  // Coordinator/gateway activity stays unassigned; never imply it belongs to a selected project.
  return entries
    .filter((entry) => !project || entry.task?.project === project)
    .sort((a, b) => b.at - a.at);
}
