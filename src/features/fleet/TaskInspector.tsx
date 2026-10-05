import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/Button';
import { IconChevronLeft, IconExternalLink } from '@/components/ui/icons';
import { apiClient } from '@/services/api/client';
import { fleetApi, type FleetStatus, type Task } from './api';
import { accountName, controlChatLink, resultPreview, stamp } from './presentation';
import styles from './FleetActivityPage.module.scss';

export function State({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <span className={styles.state} data-state={value}>
      {t('fleet.states.' + value, { defaultValue: value })}
    </span>
  );
}

// A small, text-only renderer. Provider output never becomes HTML; file paths remain text.
function inline(value: string): ReactNode[] {
  return value.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, index) => {
    if (part.startsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (link) {
      let safe = false;
      try {
        const url = new URL(link[2]);
        safe = url.protocol === 'https:' && !url.username && !url.password;
      } catch {
        /* Local file references are descriptive text. */
      }
      return safe ? (
        <a key={index} href={link[2]} target="_blank" rel="noopener noreferrer">
          {link[1]}
        </a>
      ) : (
        <span key={index}>{link[1]}</span>
      );
    }
    return part;
  });
}
export function ResultText({ value }: { value: string }) {
  return (
    <div className={styles.prose}>
      {value.split(/\n\s*\n/).map((block, index) => {
        if (block.trim().startsWith('```'))
          return <pre key={index}>{block.replace(/^```[^\n]*\n?/, '').replace(/\n?```$/, '')}</pre>;
        const lines = block.split('\n');
        if (lines.every((line) => /^\s*(?:[-*]|\d+\.)\s+/.test(line)))
          return (
            <ul key={index}>
              {lines.map((line, i) => (
                <li key={i}>{inline(line.replace(/^\s*(?:[-*]|\d+\.)\s+/, ''))}</li>
              ))}
            </ul>
          );
        if (/^#{1,6}\s/.test(block))
          return <h3 key={index}>{inline(block.replace(/^#{1,6}\s+/, ''))}</h3>;
        return <p key={index}>{inline(block)}</p>;
      })}
    </div>
  );
}

export function TaskInspector({
  id,
  status,
  onChange,
  onClose,
}: {
  id: string;
  status: FleetStatus;
  onChange: () => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const requestedTab = params.get('detail') || 'result';
  const tab = ['result', 'activity', 'details'].includes(requestedTab) ? requestedTab : 'result';
  const fullResult = params.get('full') === '1';
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof fleetApi.detail>> | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const summary = status.tasks.find((task) => task.id === id);
  useEffect(() => {
    heading.current?.focus();
  }, [id]);
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
  }, [id, summary?.updated, t]);
  const task: Task | undefined = detail?.task.id === id ? detail.task : summary;
  const project = status.projects.find((project) => project.id === task?.project);
  const parent = status.tasks.find((entry) => entry.id === task?.parentTask);
  const chatLink = controlChatLink(task?.originChatId ?? '');
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
  const changeTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set('detail', value);
    setParams(next);
  };
  if (!task)
    return (
      <aside className={styles.inspector} aria-busy="true">
        {error || t('fleet.loading')}
      </aside>
    );
  return (
    <aside className={styles.inspector} aria-label={t('fleet.task_detail')}>
      <div className={styles.inspectorTop}>
        <Button variant="ghost" onClick={onClose}>
          <IconChevronLeft size={16} />
          {t('fleet.back_tasks')}
        </Button>
        <State value={task.state} />
      </div>
      <p className={styles.projectLabel}>{project?.name || t('fleet.unknown_project')}</p>
      <h2 ref={heading} tabIndex={-1}>
        {task.title}
      </h2>
      <div className={styles.lineage}>
        <span>{t('fleet.control_chat')}</span>
        {chatLink ? (
          <a href={chatLink} className={styles.chatLink} title={t('fleet.open_chat')}>
            {task.originChatTitle || t('fleet.codex_chat')}
            <IconExternalLink size={14} />
          </a>
        ) : (
          <span>{t('fleet.standalone')}</span>
        )}
        {task.parentTask && (
          <>
            <span>{t('fleet.related_task')}</span>
            {parent ? (
              <Link
                to={
                  '/fleet?' +
                  new URLSearchParams({ source: 'fleet', task: parent.id, project: parent.project })
                }
              >
                {parent.title}
              </Link>
            ) : (
              <span>{t('fleet.related_unavailable')}</span>
            )}
          </>
        )}
      </div>
      <p className={styles.workerLine}>
        {accountName(task.account)} · {task.model || t('fleet.auto_model')} · {status.host}
      </p>
      {task.error && (
        <p role="alert" className={styles.error}>
          {task.error}
        </p>
      )}
      <nav className={styles.detailTabs} aria-label={t('fleet.task_sections')}>
        {['result', 'activity', 'details'].map((value) => (
          <button key={value} aria-pressed={tab === value} onClick={() => changeTab(value)}>
            {t('fleet.detail_' + value)}
          </button>
        ))}
      </nav>
      <div className={styles.detailBody}>
        {tab === 'result' && (
          <>
            {detail?.task.id !== id ? (
              <p role="status">{t('fleet.loading')}</p>
            ) : task.result ? (
              <>
                <ResultText value={fullResult ? task.result : resultPreview(task.result)} />
                {(task.result.length > 500 || task.result.includes('\n\n')) && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      const next = new URLSearchParams(params);
                      if (fullResult) next.delete('full');
                      else next.set('full', '1');
                      setParams(next);
                    }}
                  >
                    {t(fullResult ? 'fleet.show_summary' : 'fleet.read_full_result')}
                  </Button>
                )}
              </>
            ) : (
              <div className={styles.empty}>
                <h3>{t('fleet.no_result_title')}</h3>
                <p>{t('fleet.no_result_hint')}</p>
              </div>
            )}
          </>
        )}
        {tab === 'activity' && (
          <>
            <ol className={styles.commandList}>
              {(detail?.task.id === id ? detail.operations : []).map((operation) => (
                <li key={operation.item}>
                  <div className={styles.meta}>
                    <time>{stamp(operation.started)}</time>
                    <State value={operation.state} />
                    {operation.exitCode !== null && (
                      <span>{t('fleet.exit_code', { code: operation.exitCode })}</span>
                    )}
                  </div>
                  <details>
                    <summary>
                      {t('fleet.kind_' + operation.kind, { defaultValue: operation.kind })}
                    </summary>
                    <pre translate="no">{operation.command}</pre>
                  </details>
                </li>
              ))}
            </ol>
            {!detail?.operations.length && (
              <p className={styles.empty}>{t('fleet.operations_empty')}</p>
            )}
            <h3>{t('fleet.task_history')}</h3>
            <ol className={styles.history}>
              {status.events
                .filter((event) => event.task === id && event.kind !== 'progress')
                .map((event) => (
                  <li key={event.id}>
                    <time>{stamp(event.at)}</time>
                    <p>{event.message}</p>
                  </li>
                ))}
            </ol>
          </>
        )}
        {tab === 'details' && (
          <>
            <h3>{t('fleet.goal')}</h3>
            <p className={styles.goal}>{task.goal || t('fleet.loading')}</p>
            <h3>{t('fleet.routing')}</h3>
            <p>{task.route}</p>
            <details>
              <summary>{t('fleet.technical_details')}</summary>
              <dl className={styles.facts}>
                <dt>{t('fleet.session')}</dt>
                <dd translate="no">{task.session || t('fleet.not_started')}</dd>
                <dt>{t('fleet.workspace')}</dt>
                <dd translate="no">{task.workspace || t('fleet.not_started')}</dd>
                <dt>{t('fleet.branch')}</dt>
                <dd translate="no">{task.branch || '—'}</dd>
              </dl>
              <p className={styles.hint}>{t('fleet.native_session_hint')}</p>
              {task.usage && (
                <>
                  <h3>{t('fleet.usage')}</h3>
                  <pre>{task.usage}</pre>
                </>
              )}
            </details>
          </>
        )}
      </div>
      <div className={styles.taskActions}>
        {['running', 'queued', 'waiting'].includes(task.state) && (
          <Button
            variant="secondary"
            loading={busy === 'pause'}
            disabled={!!busy}
            onClick={() => void run('pause')}
          >
            {t('fleet.pause_thread')}
          </Button>
        )}
        {['paused', 'needs_input'].includes(task.state) && (
          <Button loading={busy === 'resume'} disabled={!!busy} onClick={() => void run('resume')}>
            {t('fleet.resume_thread')}
          </Button>
        )}
        {['review', 'paused', 'needs_input'].includes(task.state) && (
          <Button
            variant="ghost"
            loading={busy === 'complete'}
            disabled={!!busy}
            onClick={() => void run('complete')}
          >
            {t('fleet.mark_done')}
          </Button>
        )}
      </div>
      {task.state !== 'running' && (
        <details className={styles.continueTask}>
          <summary>{t('fleet.continue_task')}</summary>
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              void run('followup');
            }}
          >
            <label htmlFor="fleet-followup">{t('fleet.followup')}</label>
            <textarea
              id="fleet-followup"
              name="message"
              autoComplete="off"
              className="input"
              value={message}
              maxLength={16000}
              onChange={(event) => setMessage(event.target.value)}
              required
              rows={3}
            />
            <Button loading={busy === 'followup'} disabled={!!busy} type="submit">
              {t('fleet.send_followup')}
            </Button>
          </form>
        </details>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </aside>
  );
}
