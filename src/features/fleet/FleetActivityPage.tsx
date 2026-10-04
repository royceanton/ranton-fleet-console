import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { useAuthStore } from '@/stores';
import { useHeaderRefresh } from '@/hooks/useHeaderRefresh';
import { apiClient } from '@/services/api/client';
import { fleetApi, orderTasks, type FleetStatus, type Operation, type Task } from './api';
import styles from './FleetActivityPage.module.scss';

const accountName = (name: string) => (name ? name.charAt(0).toUpperCase() + name.slice(1) : '—');
const stamp = (value: number | null) =>
  value
    ? new Date(value * 1000).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
const attention = (task: Task) => ['review', 'needs_input'].includes(task.state);
type Tab = 'overview' | 'threads' | 'operations' | 'routing';

function State({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <span className={styles.state} data-state={value}>
      {t('fleet.states.' + value, { defaultValue: value })}
    </span>
  );
}

function Operations({ entries, tasks }: { entries: Operation[]; tasks: Task[] }) {
  const { t } = useTranslation();
  if (!entries.length) return <p className={styles.empty}>{t('fleet.operations_empty')}</p>;
  return (
    <ol className={styles.operations}>
      {entries.map((entry) => {
        const task = tasks.find((task) => task.id === entry.task);
        return (
          <li key={entry.task + ':' + entry.item}>
            <div className={styles.operationMeta}>
              <time>{stamp(entry.started)}</time>
              <span>{task?.title ?? t('fleet.gateway')}</span>
              <span>{accountName(task?.account ?? '')}</span>
              <State value={entry.state} />
              {entry.exitCode !== null && (
                <span>
                  {t(entry.kind === 'gateway_request' ? 'fleet.http_status' : 'fleet.exit_code', {
                    code: entry.exitCode,
                  })}
                </span>
              )}
            </div>
            <details>
              <summary>
                <span className={styles.mono}>{entry.command.split('\n')[0].slice(0, 180)}</span>
              </summary>
              <pre>{entry.command}</pre>
              <small>
                {entry.kind} ·{' '}
                {entry.finished
                  ? t('fleet.duration', {
                      seconds: Math.max(0, Math.round(entry.finished - entry.started)),
                    })
                  : t('fleet.in_progress')}
              </small>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

function TaskDetail({
  id,
  revision,
  onChange,
  onClose,
}: {
  id: string;
  revision: number;
  onChange: () => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof fleetApi.detail>> | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let canceled = false;
    const connection = apiClient.getConnectionRevision();
    fleetApi
      .detail(id)
      .then((value) => {
        if (!canceled && connection === apiClient.getConnectionRevision()) {
          setDetail(value);
          setError('');
        }
      })
      .catch(() => {
        if (!canceled && connection === apiClient.getConnectionRevision())
          setError(t('fleet.detail_error'));
      });
    return () => {
      canceled = true;
    };
  }, [id, revision, t]);
  const run = async (action: Parameters<typeof fleetApi.action>[1]) => {
    const connection = apiClient.getConnectionRevision();
    setBusy(action);
    setError('');
    try {
      await fleetApi.action(id, action, action === 'followup' ? { message } : {});
      if (connection !== apiClient.getConnectionRevision()) return;
      setMessage('');
      await onChange();
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.action_error'));
    } finally {
      setBusy('');
    }
  };
  if (!detail || detail.task.id !== id)
    return (
      <aside className={styles.detail} aria-busy="true">
        {error || t('fleet.loading')}
      </aside>
    );
  const task = detail.task;
  return (
    <aside className={styles.detail} aria-label={t('fleet.thread_detail')}>
      <div className={styles.sectionHead}>
        <h2>{task.title}</h2>
        <Button variant="ghost" size="sm" onClick={onClose}>
          {t('fleet.close')}
        </Button>
      </div>
      <div className={styles.operationMeta}>
        <State value={task.state} />
        <span>{accountName(task.account)}</span>
        <span>{task.model}</span>
      </div>
      <p>{task.route}</p>
      {task.error && (
        <p role="alert" className={styles.error}>
          {task.error}
        </p>
      )}
      <dl className={styles.facts}>
        <dt>{t('fleet.session')}</dt>
        <dd className={styles.mono}>{task.session || t('fleet.not_started')}</dd>
        <dt>{t('fleet.workspace')}</dt>
        <dd className={styles.mono}>{task.workspace || t('fleet.not_started')}</dd>
        <dt>{t('fleet.branch')}</dt>
        <dd className={styles.mono}>{task.branch || '—'}</dd>
      </dl>
      <details>
        <summary>{t('fleet.goal')}</summary>
        <pre>{task.goal}</pre>
      </details>
      {task.result && (
        <section>
          <h3>{t('fleet.result')}</h3>
          <pre className={styles.result}>{task.result}</pre>
          {task.usage && (
            <details>
              <summary>{t('fleet.usage')}</summary>
              <pre>{task.usage}</pre>
            </details>
          )}
        </section>
      )}
      <div className={styles.actions}>
        {['running', 'queued', 'waiting'].includes(task.state) && (
          <Button variant="secondary" disabled={!!busy} onClick={() => void run('pause')}>
            {t('fleet.pause_thread')}
          </Button>
        )}
        {['paused', 'needs_input'].includes(task.state) && (
          <Button disabled={!!busy} onClick={() => void run('resume')}>
            {t('fleet.resume_thread')}
          </Button>
        )}
        {['review', 'paused', 'needs_input'].includes(task.state) && (
          <Button variant="secondary" disabled={!!busy} onClick={() => void run('complete')}>
            {t('fleet.mark_reviewed')}
          </Button>
        )}
      </div>
      {task.state !== 'running' && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run('followup');
          }}
          className={styles.form}
        >
          <label htmlFor="fleet-followup">{t('fleet.followup')}</label>
          <textarea
            id="fleet-followup"
            className="input"
            value={message}
            maxLength={16000}
            onChange={(event) => setMessage(event.target.value)}
            required
            rows={3}
          />
          <Button loading={busy === 'followup'} disabled={!!busy || !message.trim()} type="submit">
            {t('fleet.send_followup')}
          </Button>
        </form>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <h3>{t('fleet.operations')}</h3>
      <Operations entries={detail.operations} tasks={[task]} />
    </aside>
  );
}

function SubmitTask({
  status,
  onSubmitted,
}: {
  status: FleetStatus;
  onSubmitted: (id: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [project, setProject] = useState(status.projects[0]?.id ?? '');
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [mode, setMode] = useState('read-only');
  const [priority, setPriority] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef({ signature: '', key: '' });
  const selected = status.projects.find((entry) => entry.id === project);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    setError('');
    const data = { project, title, goal, mode, priority, timeout: 1200 };
    const signature = JSON.stringify(data);
    if (signature !== submission.current.signature)
      submission.current = { signature, key: crypto.randomUUID() };
    try {
      const task = await fleetApi.submit({ ...data, idempotency: submission.current.key });
      if (connection !== apiClient.getConnectionRevision()) return;
      setTitle('');
      setGoal('');
      submission.current = { signature: '', key: '' };
      await onSubmitted(task.id);
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.submit_error'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className={styles.compose}>
      <summary>{t('fleet.new_task')}</summary>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <label htmlFor="fleet-project">{t('fleet.project')}</label>
        <select
          id="fleet-project"
          className="input"
          value={project}
          required
          onChange={(event) => {
            setProject(event.target.value);
            setMode('read-only');
          }}
        >
          {status.projects.map((entry) => (
            <option value={entry.id} key={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
        <Input
          label={t('fleet.title')}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          maxLength={120}
        />
        <label htmlFor="fleet-goal">{t('fleet.goal')}</label>
        <textarea
          className="input"
          id="fleet-goal"
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          rows={5}
          required
          maxLength={16000}
        />
        <div className={styles.formColumns}>
          <label>
            {t('fleet.mode')}
            <select
              className="input"
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <option value="read-only">{t('fleet.analysis')}</option>
              <option value="code" disabled={!selected?.writable}>
                {t('fleet.isolated_edits')}
              </option>
            </select>
          </label>
          <label>
            {t('fleet.priority')}
            <select
              className="input"
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
            >
              <option value={0}>{t('fleet.low')}</option>
              <option value={10}>{t('fleet.normal')}</option>
              <option value={20}>{t('fleet.high')}</option>
            </select>
          </label>
        </div>
        <p className={styles.hint}>{t('fleet.submit_hint')}</p>
        <Button type="submit" loading={busy}>
          {t('fleet.queue_task')}
        </Button>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
      </form>
    </details>
  );
}

function RegisterProject({ onChange }: { onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const [path, setPath] = useState('');
  const [writable, setWritable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    setError('');
    try {
      await fleetApi.project({ path, writable });
      if (connection !== apiClient.getConnectionRevision()) return;
      setPath('');
      await onChange();
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.project_error'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className={styles.compose}>
      <summary>{t('fleet.register_project')}</summary>
      <form className={styles.form} onSubmit={(event) => void save(event)}>
        <Input
          label={t('fleet.project_path')}
          value={path}
          onChange={(event) => setPath(event.target.value)}
          required
        />
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={writable}
            onChange={(event) => setWritable(event.target.checked)}
          />
          {t('fleet.allow_edits')}
        </label>
        <Button type="submit" loading={busy}>
          {t('fleet.register_project')}
        </Button>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
      </form>
    </details>
  );
}

function Routing({ status, onChange }: { status: FleetStatus; onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const [reserve, setReserve] = useState(status.reservePercent);
  const [slots, setSlots] = useState(status.maxSlots);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    setError('');
    try {
      await fleetApi.policy(reserve, slots);
      if (connection !== apiClient.getConnectionRevision()) return;
      await onChange();
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.action_error'));
    } finally {
      setBusy(false);
    }
  };
  const decisions = status.events.filter((entry) =>
    ['assigned', 'gateway_route', 'policy'].includes(entry.kind)
  );
  return (
    <section className={styles.routing}>
      <div className={styles.sectionHead}>
        <h2>{t('fleet.routing')}</h2>
        <span>{t('fleet.policy_name')}</span>
      </div>
      <p>{t('fleet.routing_hint')}</p>
      <ol className={styles.choices}>
        {status.choices.map((choice, index) => (
          <li key={choice.account}>
            <span className={styles.rank}>{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{accountName(choice.account)}</strong>
              <p>{choice.reason}</p>
            </div>
            <div>
              <State value={choice.eligible ? 'ready' : 'waiting'} />
              <p>
                {t('fleet.resets')} {stamp(choice.reset)}
              </p>
            </div>
          </li>
        ))}
      </ol>
      <details className={styles.compose}>
        <summary>{t('fleet.edit_policy')}</summary>
        <form className={styles.form} onSubmit={(event) => void save(event)}>
          <Input
            label={t('fleet.reserve')}
            type="number"
            min={5}
            max={50}
            value={reserve}
            onChange={(event) => setReserve(Number(event.target.value))}
            required
          />
          <Input
            label={t('fleet.slots')}
            type="number"
            min={1}
            max={2}
            value={slots}
            onChange={(event) => setSlots(Number(event.target.value))}
            required
          />
          <p className={styles.hint}>{t('fleet.policy_hint')}</p>
          <Button type="submit" loading={busy}>
            {t('fleet.save_policy')}
          </Button>
          {error && (
            <p role="alert" className={styles.error}>
              {error}
            </p>
          )}
        </form>
      </details>
      <h3>{t('fleet.decisions')}</h3>
      <ol className={styles.events}>
        {decisions.slice(0, 20).map((event) => {
          let message = event.message;
          if (event.kind === 'gateway_route') {
            try {
              const value: unknown = JSON.parse(message);
              if (
                value &&
                typeof value === 'object' &&
                'reason' in value &&
                typeof value.reason === 'string'
              )
                message = [
                  'account' in value && typeof value.account === 'string'
                    ? accountName(value.account)
                    : '—',
                  'model' in value && typeof value.model === 'string' ? value.model : '',
                  'session' in value && typeof value.session === 'string' ? value.session : '',
                  value.reason,
                ]
                  .filter(Boolean)
                  .join(' · ');
            } catch {
              /* Preserve safe original evidence. */
            }
          }
          return (
            <li key={event.id}>
              <time>{stamp(event.at)}</time>
              <span>{event.kind}</span>
              <p>{message}</p>
            </li>
          );
        })}
      </ol>
      {!decisions.length && <p className={styles.empty}>{t('fleet.decisions_empty')}</p>}
    </section>
  );
}

export function FleetActivityPage() {
  const { t } = useTranslation();
  const connected = useAuthStore((state) => state.connectionStatus === 'connected');
  const apiBase = useAuthStore((state) => state.apiBase);
  const [status, setStatus] = useState<FleetStatus | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('all');
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
    setSelected('');
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
  const toggle = async () => {
    if (!status) return;
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    try {
      await fleetApi.dispatch(!status.dispatchEnabled);
      if (connection !== apiClient.getConnectionRevision()) return;
      await refresh();
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.action_error'));
    } finally {
      setBusy(false);
    }
  };
  const tasks = status ? orderTasks(status.tasks) : [];
  const visible = tasks.filter(
    (task) =>
      filter === 'all' ||
      (filter === 'attention'
        ? attention(task)
        : ['running', 'queued', 'waiting'].includes(task.state))
  );
  const tasksView = (
    <section>
      <div className={styles.sectionHead}>
        <h2>{t('fleet.threads')}</h2>
        <select
          aria-label={t('fleet.filter')}
          className="input"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">{t('fleet.all')}</option>
          <option value="attention">{t('fleet.attention')}</option>
          <option value="active">{t('fleet.active')}</option>
        </select>
      </div>
      <div className={styles.threads}>
        {visible.map((task) => (
          <button
            className={styles.thread}
            key={task.id}
            onClick={() => setSelected(task.id)}
            aria-pressed={selected === task.id}
          >
            <span>
              <strong>{task.title}</strong>
              <small>
                {status?.projects.find((entry) => entry.id === task.project)?.name} ·{' '}
                {task.model || t('fleet.automatic_model')}
              </small>
            </span>
            <span>{accountName(task.account)}</span>
            <State value={task.state} />
            <span className={styles.arrow}>↗</span>
          </button>
        ))}
      </div>
      {!visible.length && <p className={styles.empty}>{t('fleet.threads_empty')}</p>}
    </section>
  );
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>{t('fleet.eyebrow')}</p>
          <h1>{t('fleet.heading')}</h1>
          <p>{t('fleet.subtitle')}</p>
        </div>
        <div className={styles.live} role="status">
          <span className={styles.dot} data-live={!!status && !error} />
          {error ? t('fleet.offline') : status ? t('fleet.live') : t('fleet.loading')}
        </div>
      </header>
      {error && (
        <div role="alert" className={styles.error}>
          {error}
          <Button variant="secondary" size="sm" onClick={() => void refresh()}>
            {t('fleet.retry')}
          </Button>
        </div>
      )}
      {!status ? (
        <div className={styles.loading} aria-busy="true">
          <p>{t('fleet.loading_hint')}</p>
        </div>
      ) : (
        <>
          <div className={styles.toolbar}>
            <div className={styles.tabs} role="group" aria-label={t('fleet.views')}>
              {(['overview', 'threads', 'operations', 'routing'] as const).map((value) => (
                <Button
                  key={value}
                  variant={tab === value ? 'secondary' : 'ghost'}
                  aria-pressed={tab === value}
                  onClick={() => setTab(value)}
                >
                  {t('fleet.' + value)}
                </Button>
              ))}
            </div>
            <Button variant="secondary" loading={busy} onClick={() => void toggle()}>
              {t(status.dispatchEnabled ? 'fleet.pause_new' : 'fleet.enable_new')}
            </Button>
          </div>
          {tab === 'overview' && (
            <>
              <section className={styles.flow} aria-label={t('fleet.data_flow')}>
                <div className={styles.accountLanes}>
                  {status.accounts.map((account) => (
                    <div
                      className={styles.accountLane}
                      key={account.alias}
                      data-selected={account.alias === status.nextAccount}
                    >
                      <div>
                        <strong>{accountName(account.alias)}</strong>
                        <State value={account.eligible ? 'ready' : 'waiting'} />
                      </div>
                      {account.windows.map((window, index) => (
                        <div className={styles.window} key={index}>
                          <span>
                            {window.windowDurationMins
                              ? window.windowDurationMins >= 1440
                                ? Math.round(window.windowDurationMins / 1440) + 'd'
                                : Math.round(window.windowDurationMins / 60) + 'h'
                              : t('fleet.window')}
                          </span>
                          <progress
                            max={100}
                            value={window.remainingPercent ?? undefined}
                            aria-label={t('fleet.remaining_for', {
                              account: accountName(account.alias),
                            })}
                          />
                          <strong>
                            {window.remainingPercent === null ? '—' : window.remainingPercent + '%'}
                          </strong>
                        </div>
                      ))}
                      {!account.windows.length && <p>{t('fleet.unknown_quota')}</p>}
                      <small>
                        {t('fleet.observed_age', {
                          seconds:
                            account.ageSeconds === null ? '—' : Math.round(account.ageSeconds),
                        })}
                      </small>
                    </div>
                  ))}
                </div>
                <span className={styles.connector} aria-hidden="true">
                  →
                </span>
                <div className={styles.flowNode}>
                  <span className={styles.eyebrow}>{t('fleet.router')}</span>
                  <strong>{accountName(status.nextAccount)}</strong>
                  <p>{t('fleet.next_route')}</p>
                  <small>{t('fleet.reserve_value', { percent: status.reservePercent })}</small>
                </div>
                <span className={styles.connector} aria-hidden="true">
                  →
                </span>
                <div className={styles.flowNode}>
                  <span className={styles.eyebrow}>{t('fleet.worker')}</span>
                  <strong>{status.host}</strong>
                  <p>
                    {t('fleet.slots_used', { active: status.activeSlots, max: status.maxSlots })}
                  </p>
                  <small>{t('fleet.native_execution')}</small>
                </div>
                <span className={styles.connector} aria-hidden="true">
                  →
                </span>
                <button
                  className={styles.reviewNode}
                  onClick={() => {
                    setTab('threads');
                    setFilter('attention');
                  }}
                >
                  <span className={styles.eyebrow}>{t('fleet.attention')}</span>
                  <strong>{tasks.filter(attention).length}</strong>
                  <p>{t('fleet.review_results')}</p>
                </button>
              </section>
              <p className={styles.routeReason}>{status.nextReason}</p>
              <div className={styles.gatewayStrip}>
                <span>{t('fleet.gateway')}</span>
                <span
                  className={styles.state}
                  data-state={status.gatewayConfigured ? 'ready' : 'needs_input'}
                >
                  {t(status.gatewayConfigured ? 'fleet.gateway_active' : 'fleet.gateway_inactive')}
                </span>
                <span>{t('fleet.requests_count', { count: status.gatewayRequests ?? '—' })}</span>
                <span>{t('fleet.success_count', { count: status.gatewaySuccess ?? '—' })}</span>
                <span>{t('fleet.failed_count', { count: status.gatewayFailed ?? '—' })}</span>
                <Link to="/quota">{t('fleet.open_quota')} ↗</Link>
              </div>
              <div className={styles.workLayout}>
                <div>{tasksView}</div>
                <div>
                  <SubmitTask
                    status={status}
                    onSubmitted={async (id) => {
                      setSelected(id);
                      await refresh();
                    }}
                  />
                  <RegisterProject onChange={refresh} />
                  <section className={styles.activity}>
                    <h2>{t('fleet.recent_activity')}</h2>
                    <ol className={styles.events}>
                      {status.events
                        .filter((entry) => entry.kind !== 'gateway_route')
                        .slice(0, 8)
                        .map((event) => (
                          <li key={event.id}>
                            <time>{stamp(event.at)}</time>
                            <p>{event.message}</p>
                          </li>
                        ))}
                    </ol>
                  </section>
                </div>
              </div>
            </>
          )}
          {tab === 'threads' && (
            <>
              {tasksView}
              <SubmitTask
                status={status}
                onSubmitted={async (id) => {
                  setSelected(id);
                  await refresh();
                }}
              />
              <RegisterProject onChange={refresh} />
            </>
          )}
          {tab === 'operations' && (
            <section>
              <div className={styles.sectionHead}>
                <h2>{t('fleet.operations')}</h2>
                <span>{t('fleet.journal_hint')}</span>
              </div>
              <Operations entries={status.operations} tasks={tasks} />
            </section>
          )}
          {tab === 'routing' && (
            <Routing
              key={status.reservePercent + ':' + status.maxSlots}
              status={status}
              onChange={refresh}
            />
          )}
          {selected && (
            <TaskDetail
              key={selected}
              id={selected}
              revision={status.now}
              onChange={refresh}
              onClose={() => setSelected('')}
            />
          )}
          <footer className={styles.footer}>
            <p>{t('fleet.coverage')}</p>
            <p>{t('fleet.constraints')}</p>
          </footer>
        </>
      )}
    </div>
  );
}
