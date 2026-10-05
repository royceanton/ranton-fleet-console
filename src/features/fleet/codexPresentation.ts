import type { CodexChat, CodexStatus } from './api';

export function descendantChats(chats: CodexChat[], parent: string) {
  const selected = new Set([parent]);
  let expanded = true;
  while (expanded) {
    expanded = false;
    for (const chat of chats) {
      if (chat.parent && selected.has(chat.parent) && !selected.has(chat.id)) {
        selected.add(chat.id);
        expanded = true;
      }
    }
  }
  return chats.filter((chat) => chat.id !== parent && selected.has(chat.id));
}
export function visibleChats(status: CodexStatus, project: string, filter: string, query: string) {
  const matches = status.chats
    .filter(
      (chat) =>
        !chat.parent &&
        (!project || chat.project === project) &&
        (filter !== 'working' ||
          chat.state === 'working' ||
          descendantChats(status.chats, chat.id).some((child) => child.state === 'working')) &&
        [chat.title, status.projects.find((p) => p.id === chat.project)?.name, chat.model]
          .join(' ')
          .toLowerCase()
          .includes(query.trim().toLowerCase())
    )
    .sort((a, b) => b.updated - a.updated);
  return filter === 'recent' ? matches.slice(0, 30) : matches;
}
