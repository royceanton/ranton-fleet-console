"""Pure quota admission and task affinity rules, independent of transport."""

from datetime import datetime
import math


def observed_age(snapshot, now):
    try:
        stamp = datetime.fromisoformat(snapshot["observedAt"]).timestamp()
        return max(0, now - stamp) if stamp <= now + 30 else math.inf
    except (KeyError, TypeError, ValueError):
        return math.inf


def assess(snapshot, now, reserve=10, freshness=300, model=None, cooldown=0):
    reasons = []
    deadlines = []
    if not snapshot or snapshot.get("authentication") != "chatgpt" or not snapshot.get("identityFingerprint"):
        return {"eligible": False, "reason": "Account sign-in needs attention", "reset": None}
    if observed_age(snapshot, now) > freshness:
        reasons.append("Quota observation is stale")
    if cooldown > now:
        reasons.append("Account is paused after a worker/provider error")
    buckets = snapshot.get("quotaWindows") or []
    if snapshot.get("quotaStatus") != "observed" or not buckets:
        reasons.append("Quota is unknown")
    # Conservatively admit against every reported bucket. Do not assume a
    # model-specific bucket is irrelevant without a documented mapping.
    for bucket in buckets:
        if bucket.get("rateLimitReachedType"):
            reasons.append("Provider reports a reached limit")
        windows = [bucket.get(key) for key in ("primary", "secondary") if bucket.get(key) is not None]
        if not windows:
            reasons.append("Reported bucket has no usable window")
        for window in windows:
            remaining, reset = window.get("remainingPercent"), window.get("resetsAt")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining):
                reasons.append("Remaining allowance is unknown")
            elif not 0 <= remaining <= 100:
                reasons.append("Remaining allowance is invalid")
            elif remaining <= reserve:
                reasons.append("Interactive headroom is reserved")
            if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset) or reset <= now:
                reasons.append("Window needs a fresh reset reading")
            else:
                deadlines.append(reset)
    catalog = snapshot.get("models") or []
    if not catalog:
        reasons.append("Model availability is unknown")
    if model and not any(model in (item.get("id"), item.get("model")) for item in catalog):
        reasons.append("Requested model is unavailable")
    return {"eligible": not reasons, "reason": "; ".join(dict.fromkeys(reasons)) or "Ready",
            "reset": min(deadlines) if deadlines else None}


def choose(accounts, task, active, now, reserve=10):
    """An account slot is a reservation; never launch two writers on one lane."""
    candidates = []
    reasons = {}
    for alias, snapshot in accounts.items():
        eligibility = assess(snapshot, now, reserve, model=task.get("model"), cooldown=snapshot.get("cooldownUntil", 0))
        if active.get(alias, 0):
            eligibility = {**eligibility, "eligible": False, "reason": "Account already has an active task"}
        reasons[alias] = eligibility["reason"]
        if eligibility["eligible"]:
            candidates.append((eligibility["reset"], alias))
    binding = task.get("account")
    if binding:
        if any(alias == binding for _, alias in candidates):
            return binding, "Keep the existing account and session for context continuity"
        return None, "Waiting for the bound account: " + reasons.get(binding, "Unavailable")
    if not candidates:
        return None, " · ".join(alias.title() + ": " + reason for alias, reason in reasons.items())
    candidates.sort()
    _, alias = candidates[0]
    return alias, "Earliest reset among eligible accounts; all reported windows retain interactive headroom"
