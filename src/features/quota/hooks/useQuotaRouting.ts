import { useEffect, useState } from 'react';
import { useAuthStore } from '@/stores';
import { apiClient } from '@/services/api/client';
import { fleetApi, type FleetStatus } from '@/features/fleet/api';

/** This is the coordinator's admission decision, not a second UI scheduler. */
export function useQuotaRouting(enabled: boolean) {
  const apiBase = useAuthStore((state) => state.apiBase);
  const connected = useAuthStore((state) => state.connectionStatus === 'connected');
  const [status, setStatus] = useState<FleetStatus | null>(null);
  useEffect(() => {
    setStatus(null);
    if (!enabled || !connected) return;
    let current = true;
    let busy = false;
    const revision = apiClient.getConnectionRevision();
    const refresh = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const value = await fleetApi.status();
        if (current && revision === apiClient.getConnectionRevision()) {
          setStatus((previous) => ({
            ...value,
            accountBindings:
              previous &&
              JSON.stringify(previous.accountBindings) === JSON.stringify(value.accountBindings)
                ? previous.accountBindings
                : value.accountBindings,
          }));
        }
      } catch {
        if (current && revision === apiClient.getConnectionRevision()) setStatus(null);
      } finally {
        busy = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), 15_000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      current = false;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [apiBase, connected, enabled]);
  return status;
}
