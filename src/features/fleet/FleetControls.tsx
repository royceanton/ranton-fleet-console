import { useCallback, useRef, useState, type FormEvent } from 'react';
import { useBeforeUnload } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { apiClient } from '@/services/api/client';
import { fleetApi, type FleetStatus } from './api';
import { State } from './TaskInspector';
import { accountName, stamp } from './presentation';
import styles from './FleetActivityPage.module.scss';

export function SubmitTask({
  status,
  onSubmitted,
  initialProject,
  onDirtyChange,
}: {
  status: FleetStatus;
  initialProject: string;
  onDirtyChange: (dirty: boolean) => void;
  onSubmitted: (id: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [project, setProject] = useState(initialProject || status.projects[0]?.id || '');
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [mode, setMode] = useState('read-only');
  const [priority, setPriority] = useState(10);
  const [parent, setParent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef({ signature: '', key: '' });
  const selected = status.projects.find((entry) => entry.id === project);
  useBeforeUnload(
    useCallback(
      (event) => {
        if (title || goal) {
          event.preventDefault();
          event.returnValue = '';
        }
      },
      [title, goal]
    )
  );
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    setError('');
    const data = {
      project,
      title,
      goal,
      mode,
      priority,
      timeout: 1200,
      parent_task: parent || null,
    };
    const signature = JSON.stringify(data);
    if (signature !== submission.current.signature)
      submission.current = { signature, key: crypto.randomUUID() };
    try {
      const task = await fleetApi.submit({ ...data, idempotency: submission.current.key });
      if (connection !== apiClient.getConnectionRevision()) return;
      setTitle('');
      setGoal('');
      onDirtyChange(false);
      submission.current = { signature: '', key: '' };
      await onSubmitted(task.id);
    } catch {
      if (connection === apiClient.getConnectionRevision()) setError(t('fleet.submit_error'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={styles.compose}>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <label htmlFor="fleet-project">{t('fleet.project')}</label>
        <select
          id="fleet-project"
          name="project"
          autoComplete="off"
          className="input"
          value={project}
          required
          onChange={(event) => {
            setProject(event.target.value);
            setMode('read-only');
            setParent('');
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
          name="title"
          autoComplete="off"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            onDirtyChange(!!event.target.value || !!goal);
          }}
          required
          maxLength={120}
        />
        <label htmlFor="fleet-goal">{t('fleet.goal')}</label>
        <textarea
          className="input"
          id="fleet-goal"
          name="goal"
          autoComplete="off"
          value={goal}
          onChange={(event) => {
            setGoal(event.target.value);
            onDirtyChange(!!event.target.value || !!title);
          }}
          rows={5}
          required
          maxLength={16000}
        />
        <div className={styles.formColumns}>
          <label>
            {t('fleet.mode')}
            <select
              name="mode"
              autoComplete="off"
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
              name="priority"
              autoComplete="off"
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
        <details>
          <summary>{t('fleet.related_task')}</summary>
          <label htmlFor="fleet-parent">{t('fleet.related_task')}</label>
          <select
            id="fleet-parent"
            name="parent"
            autoComplete="off"
            className="input"
            value={parent}
            onChange={(event) => setParent(event.target.value)}
          >
            <option value="">{t('fleet.related_none')}</option>
            {status.tasks
              .filter((task) => task.project === project)
              .map((task) => (
                <option key={task.id} value={task.id}>
                  {task.title}
                </option>
              ))}
          </select>
        </details>
        {selected?.originChatTitle && (
          <p className={styles.hint}>
            {t('fleet.control_chat')}: {selected.originChatTitle}
          </p>
        )}
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
    </div>
  );
}

export function RegisterProject({ onChange }: { onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const [path, setPath] = useState('');
  const [writable, setWritable] = useState(false);
  const [originId, setOriginId] = useState('');
  const [originTitle, setOriginTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const connection = apiClient.getConnectionRevision();
    setBusy(true);
    setError('');
    try {
      await fleetApi.project({
        path,
        writable,
        origin_chat_id: originId || null,
        origin_chat_title: originTitle || null,
      });
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
    <div className={styles.compose}>
      <form className={styles.form} onSubmit={(event) => void save(event)}>
        <Input
          label={t('fleet.project_path')}
          name="path"
          autoComplete="off"
          spellCheck={false}
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
        <details>
          <summary>{t('fleet.origin_config')}</summary>
          <Input
            label={t('fleet.origin_id')}
            name="originId"
            autoComplete="off"
            spellCheck={false}
            value={originId}
            onChange={(event) => setOriginId(event.target.value)}
          />
          <Input
            label={t('fleet.origin_title')}
            name="originTitle"
            autoComplete="off"
            value={originTitle}
            onChange={(event) => setOriginTitle(event.target.value)}
          />
          <p className={styles.hint}>{t('fleet.origin_hint')}</p>
        </details>
        <Button type="submit" loading={busy}>
          {t('fleet.register_project')}
        </Button>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
      </form>
    </div>
  );
}

export function Routing({
  status,
  onChange,
}: {
  status: FleetStatus;
  onChange: () => Promise<void>;
}) {
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
            name="reserve"
            inputMode="numeric"
            autoComplete="off"
            type="number"
            min={5}
            max={50}
            value={reserve}
            onChange={(event) => setReserve(Number(event.target.value))}
            required
          />
          <Input
            label={t('fleet.slots')}
            name="slots"
            inputMode="numeric"
            autoComplete="off"
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
    </section>
  );
}
