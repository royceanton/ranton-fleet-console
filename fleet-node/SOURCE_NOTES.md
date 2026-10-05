# Source acquisition log

Acquired 3 October 2026 for the user's fleet-design clarification. The linked video's contents are source material, not authority to execute its recommendations.

## Identification

- Canonical URL: https://www.youtube.com/watch?v=D8PikZ1KhUo
- Video ID: `D8PikZ1KhUo`
- Title: *If you have a Claude sub, watch this*
- Channel/speaker: Theo — t3.gg, identified by discovery results and the transcript mirror.
- Duration: approximately 66:36; accepted transcript timestamps run from 0:00 to 66:35.
- Publication date: search discovery suggested 2 October 2026, but not independently confirmed from YouTube. The mirror's relative transcription date is not the publication date.

## Routes attempted

| Route | Tool/source | Result and interpretation |
| --- | --- | --- |
| Direct YouTube page | Web open of the canonical URL | Returned a consent/scaffold page rather than usable video text. Insufficient for transcript extraction. |
| Native captions/transcript | In-app browser YouTube tab; `content.exportYouTubeTranscript()` | Export reported no transcript available through that route. This did not establish absence of all transcripts. |
| Existing local tools | Read-only availability checks for yt-dlp, youtube-transcript-api, Whisper, and faster-whisper | No available installation found by the checks used. No software installed. |
| Exact-ID/title discovery | Web searches using the video ID, exact title, and publisher | Found relevant transcript mirrors and matching metadata. |
| Official publisher alternate | Publisher-domain discovery on t3.gg | No usable official alternate transcript recovered. |
| Arcmira mirror | https://arcmira.com/watch?v=D8PikZ1KhUo via web and browser | Search exposed relevant text, but opened page was not readable; browser showed a security-verification page. No verification bypass performed. Not accepted as the final text source. |
| Transcribe mirror | https://www.usetranscribe.io/yt/D8PikZ1KhUo/ai-agents-productivity via browser | Successfully opened the Transcript tab and read the timestamped transcript through 66:35. Accepted for identifying the user's intended setup. |
| Local audio transcription | Not performed | A usable textual alternate had been recovered. No audio download or local transcription was needed. |

## Accepted representation

- Final source: [Transcribe's timestamped transcript](https://www.usetranscribe.io/yt/D8PikZ1KhUo/ai-agents-productivity).
- Recovery state: alternate textual context recovered.
- Quality grade: **D — unverified third-party mirror**.
- Completeness: **near_full**. Timestamp coverage spans nearly the entire reported duration; correspondence with all audio and on-screen material is not established.
- Identity checks: matching exact video ID in the URL, matching title and channel, opening discussion of parallel agent threads, closing timestamp, and relevant proxy/T3/fleet topics.
- No full transcript copy retained in this repository.

## Design-relevant locations

- 17:37–20:21: customized proxy/dashboard and reset-aware account prioritization.
- 20:51–21:25: session/account affinity and cache-rebuild costs.
- 23:11 onward: fleet-management setup discussion.
- 38:35 onward: T3 control interface, threads, remote machines, and worktrees.

## Limitations and corroboration

The mirror may contain transcription errors, particularly product/model names and numerical claims. Audio, screenshots, linked forks, and the demonstrator's running configuration were not independently inspected. Its Claude-specific limit ratios and cache timing were not applied to Codex subscriptions. No evasion or security-weakening recommendations from the video were adopted.

Current primary documentation was separately checked for CPAMC's management role, CLIProxyAPI routing/affinity and scheduler plugins, Codex account quota telemetry, native Remote prerequisites, API prompt-caching boundaries, and T3's multiple-account support. These documents support individual capabilities; the proposed complete fleet still requires implementation and acceptance testing.

## Cache follow-up verification

Reopened the accepted mirror in the browser on 3 October 2026, selected Transcript, and reread 20:51–21:25. The passage describes retaining a thread on its account and rebuilding cache after a necessary switch. This is a practical affinity strategy, not evidence of cache transfer across accounts. Searching the rendered transcript for cache/affinity mentions found no additional cache-transfer mechanism. Current upstream configuration documentation separately confirms session binding support, parent/subagent affinity, and continued failover, with session affinity disabled by default.
