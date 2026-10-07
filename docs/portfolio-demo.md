# Hyperion portfolio demo

Record a **60-second walkthrough** with one connected story: spot an issue,
inspect the service, find an endpoint, and understand background work. This
shows Hyperion's useful behavior and its visual character with six clear beats.

The first 50 seconds stand alone. Keep Activity as the final 10 seconds when
showing operational history matters to the portfolio audience.

## Prepare

From the repository root, run:

```sh
npm run demo
```

Open `http://127.0.0.1:8792/overview`. Use a **1440 × 900 content viewport at
100% browser zoom**; capture the browser content. Wait for the fonts and evidence
to appear before starting. Export at the capture resolution so the dense type
stays sharp. For a 16:9 portfolio player, place this capture on a 1920 × 1080
canvas without stretching its aspect ratio.

The launcher uses the real application and reconciler with fixture providers.
Every run gets disposable state and cannot dispatch host operations. The
header says **Fixture data**, which should remain visible in the video. Its
local authentication middleware belongs only to this launcher. Production
security is unchanged. Restart the command for a fresh take. For another port,
use `npm run demo -- --port 8794`.

## Exact sequence

| Time | Action | What the viewer should understand |
| --- | --- | --- |
| 0–7s | Start on **Overview**. Hold on the current attention list and host readings. | One place to see the deployed landscape and its current condition. |
| 7–17s | Click **Authentik** in Needs attention. In the Atlas search, type `authentik`. Hold on **1410 ms**, the running components, and the written slow-route reason. | A service can be degraded while its containers are healthy; the evidence explains the distinction. |
| 17–28s | Click **View logs** in the route dossier. Choose **Warnings & errors** in the log severity control. Hold on the worker retry and server error. | On-demand logs are readable, filterable, and attached to the selected service. These records are evidence, not a claimed proof of the route's root cause. |
| 28–38s | Close the log drawer. Press **Ctrl/Cmd+K**, replace the search with `vault`, and click **Copy** on The Vault. Pause on **Endpoint copied**. | Recognition and direct access take only seconds. |
| 38–50s | Click **Schedules**, then **backup.timer**. Hold on the full dossier: next run at 02:00, last result **Successful**, owner `root`, target `backup.service`. | systemd timers and cron share one inventory, with explicit ownership and observed results. |
| 50–60s | Click **Activity**. Type `restart`, pause on the operator request and identity, then clear the search at 57s and hold. | History is searchable; a recorded request remains distinct from a successful execution. |

Use a deliberate pointer movement followed by a short pause at each important
result. Keep the sequence at normal speed. A clean cut between the endpoint and
Schedules shots is useful if recording them separately. Close any unrelated
browser panels before recording.

## Suggested voiceover

> Hyperion brings everything deployed on my VPS into one calm surface. I can
> see what needs attention and jump straight to the service. Here, Authentik's
> route is slow even though its components are running. Its logs are available
> on demand, and I can isolate warnings and errors. When I need another service,
> I search for it and copy its endpoint. The same console brings systemd timers
> and cron together, showing the next run, owner, and last observed result.
> Activity keeps changes and operator requests searchable.

Let the short pauses carry the remaining time. Optional end caption:
**Hyperion · A personal operations console**.

## Repeatable rehearsal

With the demo server running, use a second terminal:

```sh
npx playwright install chromium  # once, if Chromium is not installed
npm run demo:record
```

This records the exact sequence headlessly and produces:

- `.portfolio-demo/hyperion-demo.webm` — a silent walkthrough, approximately 60 seconds.
- `.portfolio-demo/captions.srt` — suggested captions for the six beats.
- `.portfolio-demo/01-overview.png` through `06-activity.png` — stills for editing.

The recording starts with browser navigation; trim the brief opening loading
frame before aligning the captions. A manually recorded take can show your
pointer and carry the voiceover. Pass another fixture origin with
`npm run demo:record -- http://127.0.0.1:8794`.

## Changes supporting the demo

- Overview publishes each evidence source independently. Slow schedule reads
  no longer hold back host and service evidence.
- Attention comes from current service state and current filesystem usage.
  Historical warnings stay in Activity. The storage instrument shows the
  filesystem closest to capacity.
- Overview search filters services, schedules, attention, and recent activity.
  Service and schedule links open the exact selected record.
- Dossiers expose View logs directly. Log reads happen on demand, share pending
  requests, and cache responses without allowing an old selection to overwrite
  the current output.
- Atlas polling incorporates newly discovered or removed services, retaining
  the current search and valid selection.
- Filtered Atlas results keep the fleet rail compact. Schedule dossiers use
  the full row width. Mobile navigation fits the viewport, and service reasons
  remain visible on phones.
- Result labels preserve meaning: Successful, Failed, Warning, Information,
  Active, and Not observed. Unknown readings remain absent; large capacities
  use GiB/TiB. Critical fonts preload to stabilize the lettering sooner.
- Fixture uptime/boot time and schedule next-run times agree with their stated
  evidence and expressions.

Local baseline: with a 1,500 ms delay on the schedule endpoint, the old Overview
waited about 2,020 ms for useful content. After the change, host and service
content appeared in about 173 ms while schedules were still pending. These are
local Chromium fixture measurements, not production performance guarantees.

Validation: production build and frontend lint; 13 frontend unit checks; 30
provider/sampler/cron checks; 138 passing browser checks across desktop, tablet,
phone, and reduced motion, with 6 intentionally skipped project-specific checks.

A final 32-case demo check passed after the last changes, including catalog
additions/removals during search. The design detector also ran; its dense-type
and type-ramp notices refer to the intentionally compact hierarchy documented
in DESIGN.md, which this refinement preserves.

An MP4 copy can be exported without stretching the capture:

```sh
ffmpeg -i .portfolio-demo/hyperion-demo.webm -c:v libx264 -crf 18 \
  -pix_fmt yuv420p -movflags +faststart .portfolio-demo/hyperion-demo.mp4
```
