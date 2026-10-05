import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { IconRefreshCw } from '@/components/ui/icons';
import type { CodexQuotaState, CodexQuotaWindow, ResolvedTheme } from '@/types';
import type { FleetStatus } from '@/features/fleet/api';
import { useNow } from '@/hooks/useNow';
import { formatRelativeInstant } from '@/utils/quota';
import { getQuotaCacheKey } from '@/utils/quota/identity';
import {
  accountAlias,
  accountLabel,
  hasCodexObservation,
  ledgerWindows,
  nextWeeklyReset,
  quotaFresh,
  remainingPercent,
  weeklyCapacity,
} from '../codexLedgerModel';
import type { QuotaFileEntry } from '../logic';
import { QuotaCard } from './QuotaCard';
import styles from './CodexLedger.module.scss';

const numberLabel = (value: number, locale?: string) =>
  new Intl.NumberFormat(locale, {
    maximumFractionDigits: 1,
  }).format(value);
const percentLabel = (value: number, locale?: string) =>
  new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(value / 100);
const stamp = (value: number, locale?: string) =>
  new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(value);

function Bar({ value }: { value: number | null }) {
  return (
    <div className={`${styles.track} ${value === null ? styles.unknown : ''}`} aria-hidden="true">
      {value !== null && (
        <div
          className={`${styles.fill} ${value < 30 ? styles.fillLow : value < 70 ? styles.fillMedium : ''}`}
          style={{ width: `${value}%` }}
        />
      )}
    </div>
  );
}
function WindowReading({
  window,
  column,
  now,
}: {
  window?: CodexQuotaWindow;
  column: CodexQuotaWindow;
  now: number;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage;
  const value = remainingPercent(window);
  const label = column.labelKey ? t(column.labelKey, column.labelParams) : column.label;
  const reset = window?.resetAtMs;
  return (
    <div className={styles.window}>
      <div className={styles.windowHead}>
        <span>{label}</span>
        <span className={styles.value}>
          {value === null
            ? t('quota_ledger.unknown')
            : t('quota_ledger.remaining', { value: percentLabel(value, locale) })}
        </span>
      </div>
      <Bar value={value} />
      <div className={styles.reset}>
        {typeof reset === 'number' && Number.isFinite(reset) ? (
          <>
            <span>
              {reset > now
                ? t('quota_ledger.resets', { time: formatRelativeInstant(reset, now, locale) })
                : t('quota_ledger.reset_pending')}
            </span>
            <time dateTime={new Date(reset).toISOString()}>{stamp(reset, locale)}</time>
          </>
        ) : (
          <span>{window ? t('quota_ledger.reset_unknown') : t('quota_ledger.not_reported')}</span>
        )}
      </div>
    </div>
  );
}
export function CodexQuotaSummary({
  entries,
  quotas,
  routing,
}: {
  entries: QuotaFileEntry[];
  quotas: Record<string, CodexQuotaState>;
  routing: FleetStatus | null;
}) {
  const { t, i18n } = useTranslation();
  const now = useNow();
  const locale = i18n.resolvedLanguage;
  const capacity = weeklyCapacity(entries, quotas);
  const next = nextWeeklyReset(entries, quotas, now);
  const stale = entries.some(({ file }) => {
    const quota = quotas[getQuotaCacheKey(file)];
    return hasCodexObservation(quota) && !quotaFresh(quota, now);
  });
  return (
    <>
      <div className={styles.summary}>
        <section>
          <h2>{t('quota_ledger.capacity')}</h2>
          <div className={`${styles.headline} ${styles.numeric}`}>
            <strong>
              {capacity.remaining === null ? '—' : percentLabel(capacity.remaining, locale)}
            </strong>
            <span>
              {t('quota_ledger.out_of', { value: percentLabel(capacity.maximum, locale) })}
            </span>
          </div>
          <div className={styles.segments}>
            {entries.map(({ file }) => {
              const quota = quotas[getQuotaCacheKey(file)];
              const value = hasCodexObservation(quota)
                ? remainingPercent(quota.windows.find((window) => window.id === 'weekly'))
                : null;
              return (
                <div className={styles.segment} key={getQuotaCacheKey(file)}>
                  <Bar value={value} />
                  <p className={styles.caption}>{accountLabel(file, routing)}</p>
                </div>
              );
            })}
          </div>
          <p className={styles.caption}>
            {t('quota_ledger.reported', { count: capacity.reported, total: capacity.total })}
            {stale && ` · ${t('quota_ledger.cached')}`}
          </p>
        </section>
        <section>
          <h2>{t('quota_ledger.next_reset')}</h2>
          <div className={styles.headline}>
            <strong>{next ? accountLabel(next.file, routing) : '—'}</strong>
          </div>
          <p className={styles.caption}>
            {next
              ? t('quota_ledger.resets', { time: formatRelativeInstant(next.reset, now, locale) })
              : t('quota_ledger.reset_unknown')}
          </p>
          {next && (
            <p className={`${styles.caption} ${styles.numeric}`}>
              <time dateTime={new Date(next.reset).toISOString()}>{stamp(next.reset, locale)}</time>
            </p>
          )}
        </section>
        <section>
          <h2>{t('quota_ledger.next_task')}</h2>
          <div className={styles.headline}>
            <strong>
              {!routing
                ? '—'
                : !routing.dispatchEnabled
                  ? t('quota_ledger.paused')
                  : routing.nextAccount
                    ? routing.nextAccount.charAt(0).toUpperCase() + routing.nextAccount.slice(1)
                    : t('quota_ledger.waiting')}
            </strong>
          </div>
          <p className={styles.caption}>
            {routing
              ? t('quota_ledger.routing_policy', {
                  reserve: numberLabel(routing.reservePercent, locale),
                })
              : t('quota_ledger.router_unavailable')}
          </p>
          <Link className={styles.routeLink} to="/fleet?source=fleet">
            {t('quota_ledger.inspect_routing')} →
          </Link>
        </section>
      </div>
      <div className={styles.summaryFoot}>
        <p className={styles.note}>{t('quota_ledger.independent')}</p>
        <p className={styles.note}>{t('quota_ledger.auto_refresh')}</p>
      </div>
    </>
  );
}

export interface CodexLedgerProps {
  entries: QuotaFileEntry[];
  quotas: Record<string, CodexQuotaState>;
  routing: FleetStatus | null;
  selected: string;
  onSelect: (key: string) => void;
  canRefresh: boolean;
  resetting: string | null;
  resolvedTheme: ResolvedTheme;
  onRefresh: (entry: QuotaFileEntry) => void;
  onReset: (entry: QuotaFileEntry) => void;
}
export function CodexLedger(props: CodexLedgerProps) {
  const {
    entries,
    quotas,
    routing,
    selected,
    onSelect,
    canRefresh,
    resetting,
    resolvedTheme,
    onRefresh,
    onReset,
  } = props;
  const { t, i18n } = useTranslation();
  const now = useNow();
  const locale = i18n.resolvedLanguage;
  const columns = ledgerWindows(entries, quotas);
  return (
    <section className={styles.ledger} aria-label={t('quota_ledger.accounts')}>
      <div className={styles.ledgerHead}>
        <h2>{t('quota_ledger.accounts')}</h2>
        <p className={styles.note}>{t('quota_ledger.all_remaining')}</p>
      </div>
      {entries.map((entry) => {
        const key = getQuotaCacheKey(entry.file);
        const quota = quotas[key];
        const label = accountLabel(entry.file, routing);
        const alias = accountAlias(entry.file, routing);
        const choice = alias ? routing?.choices.find((choice) => choice.account === alias) : null;
        const fresh = quotaFresh(quota, now);
        const loading = quota?.status === 'loading';
        const expanded = selected === key;
        const statusKey = loading
          ? 'loading'
          : quota?.status === 'error'
            ? 'error'
            : quota?.status !== 'success'
              ? 'not_loaded'
              : fresh
                ? 'observed'
                : 'cached';
        return (
          <article className={styles.row} key={key} aria-label={label}>
            <div className={styles.rowMain}>
              <div className={styles.identity}>
                <h3 className={styles.name} translate="no">
                  {label}
                </h3>
                <div className={styles.meta}>
                  <span>Codex</span>
                  {quota?.planType && (
                    <span>
                      {quota.planType === 'prolite'
                        ? t('codex_quota.plan_prolite')
                        : quota.planType === 'pro'
                          ? t('codex_quota.plan_pro')
                          : quota.planType}
                    </span>
                  )}
                </div>
                <p className={styles.caption} role="status">
                  {t(`quota_ledger.${statusKey}`)}
                  {quota?.observedAtMs && (
                    <>
                      {' '}
                      ·{' '}
                      <time dateTime={new Date(quota.observedAtMs).toISOString()}>
                        {new Intl.DateTimeFormat(locale, {
                          hour: '2-digit',
                          minute: '2-digit',
                        }).format(quota.observedAtMs)}
                      </time>
                    </>
                  )}
                </p>
                {choice && (
                  <span
                    className={`${styles.status} ${choice.eligible ? styles.ready : styles.warning}`}
                    title={choice.reason}
                  >
                    {t(
                      choice.eligible ? 'quota_ledger.router_ready' : 'quota_ledger.router_waiting'
                    )}
                  </span>
                )}
              </div>
              <div className={styles.windows} aria-busy={loading}>
                {quota?.status === 'error' && (
                  <p className={styles.error} role="alert">
                    {t('codex_quota.load_failed', {
                      message: quota.error || t('common.unknown_error'),
                    })}
                  </p>
                )}
                {columns.length ? (
                  columns.map((column) => (
                    <WindowReading
                      key={column.id}
                      column={column}
                      now={now}
                      window={
                        hasCodexObservation(quota)
                          ? quota.windows.find((window) => window.id === column.id)
                          : undefined
                      }
                    />
                  ))
                ) : (
                  <p className={styles.note}>
                    {t(loading ? 'quota_ledger.loading' : 'quota_ledger.not_loaded')}
                  </p>
                )}
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  onClick={() => onRefresh(entry)}
                  disabled={!canRefresh || loading || resetting === key}
                  aria-label={t('quota_ledger.refresh_account', { account: label })}
                >
                  <IconRefreshCw size={14} aria-hidden="true" />
                  {t('auth_files.quota_refresh_single')}
                </button>
                <button
                  type="button"
                  onClick={() => onSelect(expanded ? '' : key)}
                  aria-expanded={expanded}
                  aria-label={t('quota_ledger.account_details', { account: label })}
                >
                  {t(expanded ? 'quota_ledger.hide_details' : 'quota_ledger.details')}
                  {expanded ? ' −' : ' +'}
                </button>
              </div>
            </div>
            {expanded && (
              <div className={styles.details}>
                <p className={styles.detailsNote}>{t('quota_ledger.detail_note')}</p>
                <QuotaCard
                  entry={entry}
                  quota={quota}
                  resolvedTheme={resolvedTheme}
                  canRefresh={canRefresh}
                  resetting={resetting === key}
                  onRefresh={() => onRefresh(entry)}
                  onReset={() => onReset(entry)}
                />
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
