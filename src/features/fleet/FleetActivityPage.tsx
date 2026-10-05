import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { IconCode, IconPlus, IconSlidersHorizontal } from '@/components/ui/icons';
import { useAuthStore } from '@/stores';
import { useHeaderRefresh } from '@/hooks/useHeaderRefresh';
import { apiClient } from '@/services/api/client';
import { fleetApi, orderTasks, type FleetStatus } from './api';
import { SubmitTask, RegisterProject, Routing } from './FleetControls';
import { State, TaskInspector } from './TaskInspector';
import { accountName, activityItems, matchesTask, projectGroups, stamp } from './presentation';
import { CodexActivity } from './CodexActivity';
import styles from './FleetActivityPage.module.scss';

type Dialog = 'task' | 'project' | 'routing' | '';
const formatNumber = (value: number) => new Intl.NumberFormat().format(value);

export function FleetActivityPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const isFleet = params.get('source') === 'fleet';
  return (
    <div className={styles.sourcePage}>
      <nav className={styles.sourceNav} aria-label={t('fleet.activity_source')}>
        <Link to="/fleet" aria-current={!isFleet ? 'page' : undefined}>
          Codex
        </Link>
        <Link to="/fleet?source=fleet" aria-current={isFleet ? 'page' : undefined}>
          {t('fleet.fleet_jobs')}
        </Link>
      </nav>
      {isFleet ? <FleetQueuePage /> : <CodexActivity />}
    </div>
  );
}

function FleetQueuePage() {
  const { t } = useTranslation();
  const connected = useAuthStore((state) => state.connectionStatus === 'connected');
  const apiBase = useAuthStore((state) => state.apiBase);
  const [params, setParams] = useSearchParams();
  const view = params.get('view') === 'activity' ? 'activity' : 'tasks';
  const project = params.get('project') || '';
  const filter = params.get('filter') || 'all';
  const query = params.get('query') || '';
  const selected = params.get('task') || '';
  const [status, setStatus] = useState<FleetStatus | null>(null);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<Dialog>('');
  const [draftDirty, setDraftDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const inflight = useRef(false);
  const mounted = useRef(false);
  const refresh = useCallback(async () => {
    if (!connected || inflight.current) return;
    inflight.current = true;
    const revision = apiClient.getConnectionRevision();
    try {
      const value = await fleetApi.status();
      if (mounted.current && revision === apiClient.getConnectionRevision()) {
        setStatus(value);
        setError('');
      }
    } catch {
      if (mounted.current && revision === apiClient.getConnectionRevision())
        setError(t('fleet.connection_error'));
    } finally {
      inflight.current = false;
    }
  }, [connected, t]);
  useEffect(() => {
    mounted.current = true;
    setStatus(null);
    setBusy(false);
    setError('');
    setDialog('');
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
    if ('task' in values && values.task !== params.get('task')) next.delete('full');
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return '/fleet?' + next;
  };
  const toggle = async () => {
    if (!status) return;
    const revision = apiClient.getConnectionRevision();
    setBusy(true);
    try {
      await fleetApi.dispatch(!status.dispatchEnabled);
      if (revision === apiClient.getConnectionRevision()) await refresh();
    } catch {
      if (revision === apiClient.getConnectionRevision()) setError(t('fleet.action_error'));
    } finally {
      setBusy(false);
    }
  };
  const tasks = status ? orderTasks(status.tasks) : [];
  const scoped = tasks.filter((task) => !project || task.project === project);
  const visible = tasks.filter((task) => matchesTask(task, project, filter, query));
  const selectedTask = tasks.find((task) => task.id === selected);
  const selectedProject = status?.projects.find((entry) => entry.id === project);
  const activity = status ? activityItems(status.operations, status.events, tasks, project) : [];
  const closeTask = () => {
    change({ task: '', detail: '', full: '' });
    window.requestAnimationFrame(() => document.getElementById('fleet-task-' + selected)?.focus());
  };
  return (
    <div className={styles.page}>
      <button
        type="button"
        className={styles.skip}
        onClick={() => document.getElementById('fleet-work')?.focus()}
      >
        {t('fleet.skip_tasks')}
      </button>
      <header className={styles.header}>
        <div>
          <h1>{t('fleet.fleet_jobs')}</h1>
          <p className={styles.desktopSubtitle}>
            {t('fleet.workers_status', {
              host: status?.host || t('fleet.coordinator'),
              active: status?.activeSlots ?? 0,
              max: status?.maxSlots ?? 2,
            })}
          </p>
          <p className={styles.mobileSubtitle}>
            {t('fleet.workers_short', {
              active: status?.activeSlots ?? 0,
              max: status?.maxSlots ?? 2,
            })}
          </p>
        </div>
        <div className={styles.headerActions}>
          <Button
            variant="secondary"
            onClick={() => setDialog('routing')}
            disabled={!status}
            aria-label={t('fleet.routing')}
            title={t('fleet.routing')}
          >
            <IconSlidersHorizontal size={16} />
            <span className={styles.routingLabel}>{t('fleet.routing')}</span>
          </Button>
          <Button onClick={() => setDialog('task')} disabled={!status}>
            <IconPlus size={16} />
            {t('fleet.new_task')}
          </Button>
        </div>
      </header>
      <div className={styles.statusBar}>
        <span className={styles.health}>
          <span className={styles.dot} data-live={!!status && !error} />
          {error
            ? t('fleet.offline')
            : status
              ? t(status.dispatchEnabled ? 'fleet.auto_routing' : 'fleet.paused')
              : t('fleet.loading')}
        </span>
        <div className={styles.accountChips}>
          {status?.accounts.map((account) => {
            const known = account.windows
              .map((window) => window.remainingPercent)
              .filter((value): value is number => value !== null);
            const available = known.length ? Math.min(...known) : null;
            const stale = account.ageSeconds === null || account.ageSeconds > 300;
            return (
              <Link
                to="/quota"
                className={styles.accountChip}
                key={account.alias}
                title={account.reason}
              >
                {accountName(account.alias)}
                <strong>
                  {available === null ? t('fleet.unknown') : formatNumber(available) + '%'}
                </strong>
                {stale && <span>{t('fleet.stale')}</span>}
              </Link>
            );
          })}
          <Link to="/quota" className={styles.quotaLink}>
            {t('fleet.view_quotas')}
          </Link>
        </div>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {!status && (
        <div className={styles.loading} role="status">
          {error || t('fleet.loading')}
        </div>
      )}
      {status && (
        <div className={styles.workspace}>
          <nav className={styles.projects} aria-label={t('fleet.projects')}>
            <div className={styles.mobileProjects}>
              <label htmlFor="fleet-project-filter">
                {t('fleet.projects')}
                <select
                  id="fleet-project-filter"
                  name="project"
                  value={project}
                  onChange={(event) =>
                    change({ project: event.target.value, task: '', detail: '', full: '' })
                  }
                >
                  <option value="">{t('fleet.all_projects')}</option>
                  {status.projects.map((entry) => (
                    <option value={entry.id} key={entry.id}>
                      {entry.name}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                variant="secondary"
                onClick={() => setDialog('project')}
                aria-label={t('fleet.register_project')}
                title={t('fleet.register_project')}
              >
                <IconPlus size={16} />
              </Button>
            </div>
            <div className={styles.projectHeading}>
              <h2>{t('fleet.projects')}</h2>
            </div>
            <Link
              to={href({ project: '', task: '', detail: '' })}
              aria-current={!project ? 'page' : undefined}
              className={styles.projectNav}
            >
              <IconCode size={18} />
              <span>{t('fleet.all_projects')}</span>
              <span className={styles.count}>
                {tasks.filter((task) => task.state !== 'completed').length}
              </span>
            </Link>
            {status.projects.map((entry) => {
              const members = tasks.filter(
                (task) => task.project === entry.id && task.state !== 'completed'
              );
              return (
                <Link
                  key={entry.id}
                  to={href({ project: entry.id, task: '', detail: '' })}
                  aria-current={project === entry.id ? 'page' : undefined}
                  className={styles.projectNav}
                >
                  <IconCode size={18} />
                  <span>
                    {entry.name}
                    <small>{entry.originChatTitle || t('fleet.standalone_project')}</small>
                  </span>
                  <span className={styles.count}>{members.length}</span>
                </Link>
              );
            })}
            <Button variant="ghost" onClick={() => setDialog('project')}>
              <IconPlus size={16} />
              {t('fleet.register_project')}
            </Button>
            <p className={styles.projectHint}>{t('fleet.project_count_hint')}</p>
          </nav>
          <section className={styles.work} id="fleet-work" tabIndex={-1}>
            <div className={styles.workHeading}>
              <h2>{selectedProject?.name || t('fleet.all_projects')}</h2>
              <nav className={styles.viewNav} aria-label={t('fleet.views')}>
                <Link
                  to={href({ view: 'tasks' })}
                  aria-current={view === 'tasks' ? 'page' : undefined}
                >
                  {t('fleet.tasks')}
                </Link>
                <Link
                  to={href({ view: 'activity', task: '', detail: '', full: '' })}
                  aria-current={view === 'activity' ? 'page' : undefined}
                >
                  {t('fleet.activity')}
                </Link>
              </nav>
            </div>
            <div className={styles.workColumns} data-detail={!!selectedTask}>
              <div className={styles.master}>
                {view === 'tasks' ? (
                  <>
                    <div className={styles.filters}>
                      <div className={styles.filterButtons} aria-label={t('fleet.filter')}>
                        {['all', 'running', 'results', 'done'].map((value) => {
                          const count = scoped.filter((task) =>
                            matchesTask(task, '', value, '')
                          ).length;
                          return (
                            <button
                              key={value}
                              aria-pressed={filter === value}
                              onClick={() => change({ filter: value })}
                            >
                              {t('fleet.filter_' + value)}
                              <span>{formatNumber(count)}</span>
                            </button>
                          );
                        })}
                      </div>
                      <Input
                        type="search"
                        name="query"
                        autoComplete="off"
                        aria-label={t('fleet.search_tasks')}
                        placeholder={t('fleet.search_placeholder')}
                        value={query}
                        onChange={(event) => change({ query: event.target.value }, true)}
                      />
                    </div>
                    <div className={styles.taskGroups}>
                      {projectGroups(status.projects, visible).map((group) => (
                        <section key={group.project?.id || 'unknown'}>
                          <h3>
                            {group.project?.name || t('fleet.unknown_project')}
                            <span>{group.tasks.length}</span>
                          </h3>
                          {group.tasks.map((task) => {
                            const latest = status.operations.find(
                              (operation) => operation.task === task.id
                            );
                            const current = status.events.find(
                              (event) =>
                                event.task === task.id &&
                                !['assigned', 'queued'].includes(event.kind)
                            );
                            return (
                              <Link
                                id={'fleet-task-' + task.id}
                                key={task.id}
                                className={styles.taskRow}
                                to={href({ task: task.id, detail: 'result', view: 'tasks' })}
                                aria-current={selected === task.id ? 'page' : undefined}
                              >
                                <div className={styles.taskMain}>
                                  <div>
                                    <strong>{task.title}</strong>
                                    <State value={task.state} />
                                  </div>
                                  <span className={styles.originLabel}>
                                    {t('fleet.control_chat')}:{' '}
                                    {task.originChatTitle || t('fleet.standalone')}
                                  </span>
                                  {task.state === 'running' && (
                                    <p className={styles.currentAction}>
                                      {latest
                                        ? t('fleet.kind_' + latest.kind, {
                                            defaultValue: latest.kind,
                                          })
                                        : current?.message || t('fleet.starting')}
                                    </p>
                                  )}
                                </div>
                                <div className={styles.taskMeta}>
                                  <span>
                                    {task.account
                                      ? accountName(task.account)
                                      : t('fleet.auto_account')}
                                  </span>
                                  <time>{stamp(task.updated)}</time>
                                </div>
                              </Link>
                            );
                          })}
                        </section>
                      ))}
                      {!visible.length && (
                        <div className={styles.empty}>
                          <h3>{t('fleet.no_matching_tasks')}</h3>
                          <p>{t('fleet.no_matching_hint')}</p>
                          <Button variant="secondary" onClick={() => setDialog('task')}>
                            {t('fleet.new_task')}
                          </Button>
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <p className={styles.activityHint}>{t('fleet.activity_scope')}</p>
                    <ol className={styles.activityList}>
                      {activity.map((entry) => {
                        const member = status.projects.find(
                          (project) => project.id === entry.task?.project
                        );
                        const operation = entry.operation;
                        return (
                          <li key={entry.id}>
                            <time>{stamp(entry.at)}</time>
                            <div className={styles.activityContent}>
                              {entry.task ? (
                                <Link
                                  to={href({
                                    task: entry.task.id,
                                    view: 'tasks',
                                    detail: 'activity',
                                  })}
                                >
                                  {entry.task.title}
                                </Link>
                              ) : (
                                <strong>
                                  {t(operation ? 'fleet.gateway' : 'fleet.coordinator')}
                                </strong>
                              )}
                              <small>
                                {member?.name || t('fleet.unassigned')}
                                {entry.task?.originChatTitle
                                  ? ' · ' + entry.task.originChatTitle
                                  : ''}
                              </small>
                              {operation ? (
                                <details>
                                  <summary>
                                    {t('fleet.kind_' + operation.kind, {
                                      defaultValue: operation.kind,
                                    })}
                                  </summary>
                                  <pre translate="no">{operation.command}</pre>
                                  {operation.exitCode !== null && (
                                    <p>
                                      {t(
                                        operation.kind === 'gateway_request'
                                          ? 'fleet.http_status'
                                          : 'fleet.exit_code',
                                        { code: operation.exitCode }
                                      )}
                                    </p>
                                  )}
                                </details>
                              ) : (
                                <p>{entry.event?.message}</p>
                              )}
                            </div>
                            {operation && <State value={operation.state} />}
                          </li>
                        );
                      })}
                    </ol>
                    {!activity.length && (
                      <div className={styles.empty}>{t('fleet.operations_empty')}</div>
                    )}
                  </>
                )}
              </div>
              {selectedTask && (
                <TaskInspector
                  key={selectedTask.id}
                  id={selectedTask.id}
                  status={status}
                  onChange={refresh}
                  onClose={closeTask}
                />
              )}
            </div>
          </section>
        </div>
      )}
      {status && (
        <>
          <Modal
            open={dialog === 'task'}
            title={t('fleet.new_task')}
            onClose={() => {
              if (!draftDirty || window.confirm(t('fleet.discard_draft'))) {
                setDraftDirty(false);
                setDialog('');
              }
            }}
            width={620}
          >
            <SubmitTask
              status={status}
              initialProject={project}
              onDirtyChange={setDraftDirty}
              onSubmitted={async (id) => {
                setDialog('');
                await refresh();
                change({ task: id, detail: 'result', view: 'tasks', filter: 'all', full: '' });
              }}
            />
          </Modal>
          <Modal
            open={dialog === 'project'}
            title={t('fleet.register_project')}
            onClose={() => setDialog('')}
            width={560}
          >
            <RegisterProject
              onChange={async () => {
                setDialog('');
                await refresh();
              }}
            />
          </Modal>
          <Modal
            open={dialog === 'routing'}
            title={t('fleet.routing')}
            onClose={() => setDialog('')}
            width={640}
          >
            <p className={styles.meta}>
              {t(status.gatewayConfigured ? 'fleet.gateway_active' : 'fleet.gateway_unavailable')}
            </p>
            <Routing status={status} onChange={refresh} />
            <Button variant="secondary" loading={busy} onClick={() => void toggle()}>
              {t(status.dispatchEnabled ? 'fleet.pause_dispatch' : 'fleet.enable_dispatch')}
            </Button>
          </Modal>
        </>
      )}
    </div>
  );
}
