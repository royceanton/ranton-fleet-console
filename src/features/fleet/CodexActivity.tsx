import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { IconCode, IconExternalLink } from '@/components/ui/icons';
import { useAuthStore } from '@/stores';
import { useHeaderRefresh } from '@/hooks/useHeaderRefresh';
import { apiClient } from '@/services/api/client';
import { codexApi, type CodexChat, type CodexOperation, type CodexStatus } from './api';
import { controlChatLink, stamp } from './presentation';
import { visibleChats } from './codexPresentation';
import styles from './FleetActivityPage.module.scss';

type Detail = Awaited<ReturnType<typeof codexApi.detail>>;
function ChatState({ value }: { value: string }) {
  const { t } = useTranslation();
  const state = ['working', 'idle', 'failed', 'interrupted', 'unfinished'].includes(value)
    ? value
    : 'unknown';
  return (
    <span className={styles.state} data-state={state === 'working' ? 'running' : state}>
      {t('fleet.codex_states.' + state)}
    </span>
  );
}
export function CodexActivity() {
  const { t } = useTranslation();
  const connected = useAuthStore((state) => state.connectionStatus === 'connected');
  const apiBase = useAuthStore((state) => state.apiBase);
  const [params, setParams] = useSearchParams();
  const project = params.get('project') || '';
  const selected = params.get('chat') || '';
  const query = params.get('query') || '';
  const filter = ['working', 'all'].includes(params.get('filter') || '')
    ? params.get('filter')!
    : 'recent';
  const activity = params.get('view') === 'activity';
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState('');
  const mounted = useRef(false);
  const inflight = useRef(false);
  const previousSelection = useRef('');
  const refresh = useCallback(async () => {
    if (!connected || inflight.current) return;
    inflight.current = true;
    const revision = apiClient.getConnectionRevision();
    try {
      const value = await codexApi.status();
      if (mounted.current && revision === apiClient.getConnectionRevision()) {
        setStatus(value);
        setError('');
      }
    } catch {
      if (mounted.current && revision === apiClient.getConnectionRevision())
        setError(t('fleet.codex_connection_error'));
    } finally {
      inflight.current = false;
    }
  }, [connected, t]);
  useEffect(() => {
    mounted.current = true;
    setStatus(null);
    setError('');
    void refresh();
    const interval = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
    };
  }, [apiBase, refresh]);
  useHeaderRefresh(refresh, connected);
  useEffect(() => {
    if (!status?.available) return;
    const next = new URLSearchParams(params);
    for (const key of ['task', 'detail', 'full']) next.delete(key);
    if (project && !status.projects.some((entry) => entry.id === project)) next.delete('project');
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [params, project, setParams, status]);
  useEffect(() => {
    let live = true;
    let pending = false;
    setDetail(null);
    setDetailError('');
    const refreshDetail = async () => {
      if (!selected || !connected || pending) return;
      pending = true;
      const revision = apiClient.getConnectionRevision();
      try {
        const value = await codexApi.detail(selected);
        if (live && revision === apiClient.getConnectionRevision()) {
          setDetail(value);
          setDetailError('');
        }
      } catch {
        if (live && revision === apiClient.getConnectionRevision())
          setDetailError(t('fleet.codex_detail_error'));
      } finally {
        pending = false;
      }
    };
    void refreshDetail();
    const interval = window.setInterval(() => {
      if (!document.hidden) void refreshDetail();
    }, 5000);
    return () => {
      live = false;
      window.clearInterval(interval);
    };
  }, [apiBase, connected, selected, t]);
  useEffect(() => {
    if (selected) document.getElementById('codex-chat-title')?.focus();
    else if (previousSelection.current)
      (
        document.getElementById('codex-chat-' + previousSelection.current) ||
        document.getElementById('codex-work')
      )?.focus();
    previousSelection.current = selected;
  }, [selected, detail?.chat.id]);
  const change = (values: Record<string, string>, replace = false) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace });
  };
  const href = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    next.delete('source');
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return '/fleet?' + next;
  };
  const close = () => {
    change({ chat: '' });
  };
  const scoped = status?.chats.filter((chat) => !project || chat.project === project) || [];
  const roots = scoped.filter((chat) => !chat.parent);
  const chats = status ? visibleChats(status, project, filter, query) : [];
  const projectName =
    status?.projects.find((p) => p.id === project)?.name || t('fleet.all_projects');
  const selectedChat = status?.chats.find((chat) => chat.id === selected);
  const operations =
    status?.operations.filter((op) => {
      const chat = status.chats.find((c) => c.id === op.chat);
      return chat && (!project || chat.project === project);
    }) || [];
  const readProblem = status?.errorCode
    ? t(
        'fleet.codex_read_errors.' +
          (['metadata_updating', 'database_busy', 'unsafe_data_path'].includes(status.errorCode)
            ? status.errorCode
            : 'records_unreadable')
      )
    : '';
  return (
    <div className={styles.page}>
      <button
        className={styles.skip}
        onClick={() => document.getElementById('codex-work')?.focus()}
      >
        {t('fleet.skip_chats')}
      </button>
      <header className={styles.header}>
        <div>
          <h1>{t('fleet.page_title')}</h1>
          <p>{t('fleet.codex_subtitle')}</p>
        </div>
        <p className={styles.observationStamp}>
          {status ? t('fleet.observed_at', { time: stamp(status.observedAt) }) : t('fleet.loading')}
        </p>
      </header>
      <div className={styles.statusBar}>
        <span className={styles.health}>
          <span className={styles.dot} data-live={!!status?.available && !status.stale && !error} />
          {error ||
            (status?.available
              ? status.stale
                ? t('fleet.codex_stale')
                : t('fleet.codex_connected', {
                    projects: status.projects.length,
                    working: status.chats.filter((c) => c.state === 'working').length,
                  })
              : status
                ? t('fleet.codex_unavailable')
                : t('fleet.loading'))}
        </span>
        <span className={styles.count}>{t('fleet.codex_read_only')}</span>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {status && !status.available && (
        <p role="status" className={styles.empty}>
          {t('fleet.codex_unavailable_hint')} {readProblem}
        </p>
      )}
      {status?.available && status.stale && (
        <p role="status" className={styles.hint}>
          {t('fleet.codex_stale_hint')} {readProblem}
        </p>
      )}
      {!status && !error && (
        <p role="status" className={styles.loading}>
          {t('fleet.loading')}
        </p>
      )}
      {status?.available && (
        <div className={styles.workspace}>
          <nav className={styles.projects} aria-label={t('fleet.codex_projects')}>
            <div className={styles.mobileProjects}>
              <label htmlFor="codex-project-filter">
                {t('fleet.projects')}
                <select
                  id="codex-project-filter"
                  name="project"
                  value={project}
                  onChange={(event) => change({ project: event.target.value, chat: '' })}
                >
                  <option value="">{t('fleet.all_projects')}</option>
                  {status.projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className={styles.projectHeading}>
              <h2>{t('fleet.codex_projects')}</h2>
            </div>
            <div className={styles.projectScroll}>
              <Link
                className={styles.projectNav}
                to={href({ project: '', chat: '' })}
                aria-current={!project ? 'page' : undefined}
              >
                <IconCode size={18} />
                <span>{t('fleet.all_projects')}</span>
                <span className={styles.count}>{status.chats.filter((c) => !c.parent).length}</span>
              </Link>
              {status.projects.map((p) => (
                <Link
                  key={p.id}
                  className={styles.projectNav}
                  to={href({ project: p.id, chat: '' })}
                  aria-current={p.id === project ? 'page' : undefined}
                >
                  <IconCode size={18} />
                  <span>
                    {p.name}
                    {p.workingCount > 0 && (
                      <small>{t('fleet.working_count', { count: p.workingCount })}</small>
                    )}
                  </span>
                  <span className={styles.count}>{p.chatCount}</span>
                </Link>
              ))}
            </div>
            <p className={styles.projectHint}>{t('fleet.codex_project_hint')}</p>
          </nav>
          <section className={styles.work} id="codex-work" tabIndex={-1}>
            <div className={styles.workHeading}>
              <h2>{projectName}</h2>
              <nav className={styles.viewNav} aria-label={t('fleet.views')}>
                <Link to={href({ view: 'chats' })} aria-current={!activity ? 'page' : undefined}>
                  {t('fleet.chats')}
                </Link>
                <Link
                  to={href({ view: 'activity', chat: '' })}
                  aria-current={activity ? 'page' : undefined}
                >
                  {t('fleet.activity')}
                </Link>
              </nav>
            </div>
            <div className={styles.workColumns} data-detail={!!selectedChat}>
              <div className={styles.master}>
                {activity ? (
                  <>
                    <p className={styles.activityHint}>{t('fleet.codex_activity_hint')}</p>
                    <OperationList
                      operations={operations}
                      chats={status.chats}
                      projects={status.projects}
                      href={href}
                    />
                    {!operations.length && (
                      <p className={styles.empty}>{t('fleet.codex_no_activity')}</p>
                    )}
                  </>
                ) : (
                  <>
                    <div className={styles.filters}>
                      <div
                        className={styles.filterButtons}
                        role="group"
                        aria-label={t('fleet.filter_chats')}
                      >
                        {['recent', 'working', 'all'].map((value) => (
                          <button
                            key={value}
                            type="button"
                            aria-pressed={filter === value}
                            onClick={() => change({ filter: value, chat: '' })}
                          >
                            {t('fleet.chat_filter_' + value)}
                            {value !== 'recent' && (
                              <span>
                                {value === 'all'
                                  ? roots.length
                                  : visibleChats(status, project, 'working', '').length}
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                      <Input
                        type="search"
                        name="query"
                        autoComplete="off"
                        spellCheck={false}
                        aria-label={t('fleet.search_chats')}
                        placeholder={t('fleet.search_chats_placeholder')}
                        value={query}
                        onChange={(event) => change({ query: event.target.value }, true)}
                      />
                    </div>
                    <div className={styles.chatRows}>
                      {chats.map((chat) => (
                        <Link
                          key={chat.id}
                          id={'codex-chat-' + chat.id}
                          to={href({ chat: chat.id })}
                          className={styles.taskRow}
                          aria-current={chat.id === selected ? 'page' : undefined}
                        >
                          <div className={styles.taskMain}>
                            <div>
                              <strong>{chat.title}</strong>
                              <ChatState value={chat.state} />
                            </div>
                            <span className={styles.originLabel}>
                              {status.projects.find((p) => p.id === chat.project)?.name ||
                                t('fleet.unassigned_chat')}
                              {status.chats.some((c) => c.parent === chat.id) &&
                                ' · ' +
                                  t('fleet.agents_count', {
                                    count: status.chats.filter((c) => c.parent === chat.id).length,
                                  })}
                            </span>
                          </div>
                          <div className={styles.taskMeta}>
                            <time>{stamp(chat.updated)}</time>
                          </div>
                        </Link>
                      ))}
                    </div>
                    {!chats.length && <p className={styles.empty}>{t('fleet.codex_no_chats')}</p>}
                  </>
                )}
              </div>
              {selectedChat && (
                <aside className={styles.inspector} aria-label={t('fleet.codex_chat_detail')}>
                  <div className={styles.inspectorTop}>
                    <Button variant="ghost" onClick={close}>
                      {t('fleet.back_chats')}
                    </Button>
                    <ChatState value={selectedChat.state} />
                  </div>
                  <p className={styles.projectLabel}>
                    {status.projects.find((p) => p.id === selectedChat.project)?.name ||
                      t('fleet.unassigned_chat')}
                  </p>
                  <h2 id="codex-chat-title" tabIndex={-1}>
                    {selectedChat.title}
                  </h2>
                  {controlChatLink(selectedChat.id) && (
                    <a className={styles.chatLink} href={controlChatLink(selectedChat.id)!}>
                      {t('fleet.open_codex_chat')}
                      <IconExternalLink size={14} />
                    </a>
                  )}
                  {selectedChat.parent && (
                    <p className={styles.workerLine}>
                      {t('fleet.parent_chat')}{' '}
                      <Link to={href({ chat: selectedChat.parent })}>
                        {status.chats.find((c) => c.id === selectedChat.parent)?.title ||
                          t('fleet.unknown')}
                      </Link>
                    </p>
                  )}
                  <p className={styles.workerLine}>
                    {selectedChat.model || t('fleet.unknown')} · {t('fleet.codex_local_host')}
                  </p>
                  {detailError && (
                    <p role="alert" className={styles.error}>
                      {detailError}
                    </p>
                  )}
                  <h3>{t('fleet.latest_operations')}</h3>
                  {detail?.chat.id === selected ? (
                    <>
                      {detail.stale && !status.stale && (
                        <p role="status" className={styles.hint}>
                          {t('fleet.codex_history_retry_hint')}
                        </p>
                      )}
                      <OperationList operations={detail.operations.slice(0, 6)} />
                      {detail.operations.length > 6 && (
                        <details>
                          <summary>
                            {t('fleet.older_operations', { count: detail.operations.length - 6 })}
                          </summary>
                          <OperationList operations={detail.operations.slice(6)} />
                        </details>
                      )}
                      {!detail.historyAvailable && (
                        <p className={styles.hint}>{t('fleet.codex_no_history')}</p>
                      )}
                      {detail.children.length > 0 && (
                        <details>
                          <summary>
                            {t('fleet.agents_count', { count: detail.children.length })}
                          </summary>
                          <ul className={styles.agentList}>
                            {detail.children.map((child) => (
                              <li key={child.id}>
                                <Link to={href({ chat: child.id })}>{child.title}</Link>
                                <ChatState value={child.state} />
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  ) : (
                    !detailError && <p role="status">{t('fleet.loading')}</p>
                  )}
                  <details className={styles.chatFacts}>
                    <summary>{t('fleet.workspace_details')}</summary>
                    <dl className={styles.facts}>
                      <dt>{t('fleet.workspace')}</dt>
                      <dd>{selectedChat.workspace}</dd>
                      <dt>{t('fleet.branch')}</dt>
                      <dd>{selectedChat.branch || '—'}</dd>
                      <dt>{t('fleet.chat_id')}</dt>
                      <dd>{selectedChat.id}</dd>
                      <dt>{t('fleet.saved_turn_state')}</dt>
                      <dd>{selectedChat.savedState}</dd>
                    </dl>
                    <p className={styles.hint}>{t('fleet.codex_state_hint')}</p>
                  </details>
                </aside>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
function OperationList({
  operations,
  chats,
  projects,
  href,
}: {
  operations: CodexOperation[];
  chats?: CodexChat[];
  projects?: CodexStatus['projects'];
  href?: (values: Record<string, string>) => string;
}) {
  const { t } = useTranslation();
  return (
    <ol className={styles.commandList}>
      {operations.map((op) => {
        const chat = chats?.find((c) => c.id === op.chat);
        return (
          <li key={op.chat + ':' + op.id}>
            {chat && href && (
              <>
                <Link className={styles.operationChat} to={href({ view: 'chats', chat: chat.id })}>
                  {chat.title}
                </Link>
                <small className={styles.originLabel}>
                  {projects?.find((p) => p.id === chat.project)?.name || t('fleet.unassigned_chat')}
                </small>
              </>
            )}
            <div className={styles.operationHeading}>
              <strong>{op.label}</strong>
              <time>{stamp(op.at)}</time>
            </div>
            <p className={styles.meta}>
              {t(
                'fleet.operation_state_' +
                  (['completed', 'inProgress', 'failed', 'declined'].includes(op.state)
                    ? op.state
                    : 'unknown')
              )}
              {op.exitCode !== null && ' · ' + t('fleet.exit_code', { code: op.exitCode })}
            </p>
            {op.command && (
              <details>
                <summary>{t('fleet.command')}</summary>
                <pre>{op.command}</pre>
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
