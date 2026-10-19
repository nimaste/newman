# Newman for Android

Two apps and a shared library, built from one Gradle project (spec: `../docs/apps.md`).

| Module | App | Package (release / debug) |
|---|---|---|
| `:core` | shared: API client and models, mTLS store, sign-in, live WebSocket, tile flow | library |
| `:phone` | **Newman** (phone) | `ca.nimaste.newman` / `ca.nimaste.newman.dev` |
| `:wall` | **Newman Wall** (wall tablet) | `ca.nimaste.newman.wall` / `ca.nimaste.newman.wall.dev` |

compileSdk 35, minSdk 26, targetSdk 35, JVM 17. Versions live in `gradle/libs.versions.toml`
(AGP 8.7, Kotlin 2.0, Compose BOM, Hilt, Retrofit + OkHttp 4.12 + kotlinx-serialization, Coil 3).

Nothing in the apps knows a kind of device: names, states, commands, value types and readings come
from `GET /api/meta`, so a new kind of device shows up with no app update.

```
android/
  core/src/main/java/ca/nimaste/newman/core/
    api/        NewmanApi (Retrofit), ApiErrors, LoginResult
    hub/        HubRepository (load + live + optimistic commands), HubState, HubReducer
    live/       LiveSocket (OkHttp WebSocket, backoff 1-30 s), LiveMessage,
                LiveVideo.kt: LiveVideoSession (app-owned camera socket <-> player page), LiveStreamPool (4 tiles)
    model/      Models, HubModel (port of web/src/model.js), Controls, TileFlow, PhoneTabs (which pages are tabs),
                PhoneGrid (old phone layouts, unused by the phone now), Home (GET /api/home, HomeLogic), AutomationEdit,
                TileFit (rows that fit a tile's rectangle, the rest as "+N more")
    people/     Person, LocationSettings, PeopleText (row wording), PersonLook (colour/photo), PersonRefresh, PeopleTile (who a People tile shows, fitted),
                SharingSwitch, ReportSchedule
    today/      TodayBriefing (GET /api/today), TodayView (the Today card's rows, Done / Undo / tomorrow;
                the Greeting tile's line and size)
    mtls/       MTLSStore, MTLSKeyManager + MTLSSslConfig, P12Validator
    net/        HubInterceptor + HubHttp (server, Bearer token, Cloudflare headers), ServerAddress,
                Connect (HubHello, ConnectLink, ConnectCheck: testing an address + plain-English problems,
                HubChoice: what to save, HubPlan: same hub or another when changing server), HubAddressPolicy + HubSelector (the phone's home/outside address), WallAddress, HubDiscovery (NSD)
    push/       PushPayload (the hub's FCM data fields)
    livestatus/ LiveStatuses.kt: live statuses' shapes (socket / GET /api/live/current / FCM), LiveNotifications
                (what each looks like as a notification, what to post / cancel for a new list)
    settings/   ConnectionStore (EncryptedSharedPreferences)
    widgets/    Widgets.kt: GET /api/widgets/devices + /state + /home shapes, WidgetPicker (group, search,
                order, limit), WidgetTiles (tap action, confirm, state text, tone, spoken label, cache),
                WidgetLayout (devices per size, tile lines, compact tiles / stacked rows); DeviceControls.kt:
                DeviceControlPlan (Device Controls: switch / button / status, unlock needed), UsageRank
    model/Spoken.kt  TalkBack wording ("Den plug, on") and TextScale (font-size caps for fixed cells)
  core/src/test/   unit tests + JSON captured read-only from the live hub (resources/hub)
  phone/src/main/java/ca/nimaste/newman/
    MainActivity, NewmanApp, di/AppModule, data/Account, data/PeopleRepository, data/TodayRepository
    location/   LocationSharing (settings + reports), LocationTracker (fused / LocationManager), receivers
    push/       NewmanMessagingService (FCM, incl. the silent "locate"), ActionReceiver, Notifications,
                ButtonPress (PressWorker, PressReports: notification buttons + press reports),
                LiveStatusNotifier + LiveActionReceiver (live statuses as ongoing notifications)
    security/   BiometricGate
    ui/         onboarding, home (tabs, Home, Climate, Lights, Security, Today, Edit Home), cameras, today, people (list + map),
                tiles, device sheet, camera, alerts, more (More tab, Devices, Automations + editor), me (incl. "Share my location")
                common/LiveVideo.kt: LiveCamera = still + live video (shared player page in a WebView)
    widgets/    WidgetSync (states + Home status cache, taps, live / FCM / 15-min refresh), DeviceGridWidget
                (Glance: Devices – tiles / rows), HomeStatusWidget, WidgetFaces (colours, tile / row faces),
                DevicePicker + WidgetConfigActivity / TileSettingsActivity / ConfirmActivity, QuickTiles
                (Newman 1/2/3), WidgetRefreshWorker, WidgetIcons, NewmanControlsService (Device Controls),
                LauncherShortcuts + ShortcutActivity (app-icon and home-screen shortcuts)
  wall/src/main/java/ca/nimaste/newman/wall/
    WallActivity (WebView kiosk), WallBridge (window.NewmanWall), WallSettingsActivity,
    WallApp (+ WallSettings, BootReceiver, WallAdminReceiver)
```

## Building

Everything builds in Docker on the NAS. Mount the whole Newman project, not just `android/`: the phone
packages the shared live player page from `../mobile-shared/live-player.html`. The Gradle and Android SDK caches live in
`/mnt/cache/appdata/newman-build-cache`, not in the project.

```sh
docker run --rm --name newman-android-build \
  -v /mnt/user/projects/Newman:/proj \
  -v /mnt/cache/appdata/newman-build-cache:/cache \
  -e GRADLE_USER_HOME=/cache/gradle -e ANDROID_HOME=/cache/sdk \
  -w /proj/android eclipse-temurin:17-jdk bash -c '
    # first run only: install the SDK pieces into the cache
    if [ ! -x /cache/sdk/cmdline-tools/latest/bin/sdkmanager ]; then
      cd /tmp && curl -sSLo clt.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
      mkdir -p /cache/sdk/cmdline-tools && cd /cache/sdk/cmdline-tools && jar xf /tmp/clt.zip && mv cmdline-tools latest && chmod +x latest/bin/*
      yes | latest/bin/sdkmanager --licenses >/dev/null; latest/bin/sdkmanager "platforms;android-35" "build-tools;35.0.0" "platform-tools"
    fi
    cd /proj/android && echo "sdk.dir=/cache/sdk" > local.properties
    ./gradlew --no-daemon :core:testDebugUnitTest :phone:assembleDebug :wall:assembleDebug'
chown -R chris:users /mnt/user/projects/Newman/android
```

APKs land in `phone/build/outputs/apk/debug/phone-debug.apk` and `wall/build/outputs/apk/debug/wall-debug.apk`
(`assembleRelease` for release builds, see signing below). Signed release APKs are also built by GitHub
Actions on every `v*` tag ([Release signing](#release-signing)).

### Tests

`./gradlew :core:testDebugUnitTest` runs the logic tests: parsing real `/api/meta`, `/api/entities`,
dashboards, events, alerts and push categories (captured read-only from the hub, in
`core/src/test/resources/hub`); the 4-column tile flow; the control for each command `valueType`;
the sensitive-command check; WebSocket messages, the reducer, optimistic commands and a real
reconnect against MockWebServer; `.p12` validation (valid, wrong password, expired, no key, junk);
server addresses, the request interceptor, sign-in replies and push payloads; connecting (the
newman://connect link, home-or-outside address choice, what a failed test means, hubs found over NSD); the phone's tabs
(three pages, People, More; a Today page becomes the Today card); More's Devices (sections, search,
filters) and Automations (order, last-run words); phone layouts (parsing, tab
plan, cell rectangles, devices-tile fit, the 404 fallback); the People tile (who, how many rows fit,
"+N more", one line) and the Greeting tile (by hour, its summary line, its size); the Today card's rows and
buttons; People wording ("Home", "School", "3.2 km away", "updated 4 min ago"), the sharing switch
from `GET /api/me/location`, the background report schedule, the one-shot locate, colour/photo
parsing and the tap-to-refresh states (incl. 429 and the 45 s timeout); live messages incl. `entity-removed`;
home-screen widgets (`WidgetsTest`: the hub's widget state, the tap action, "confirm first" only for
sensitive actions, exact readings, the cache, the picker's room / type groups, search, reordering and
limit, the layout per size and count, tones, the Home status reply);
Device Controls and shortcuts (`DeviceControlsTest`: switch / button / status from the hub's actions, unlock
only for sensitive taps, the most-used ranking, spoken labels, the font-size caps, and Change server's
same-hub check); live statuses (`LiveStatusesTest`: the socket's list as notifications - title, state,
counting time, buttons named per device and at most three, "N more" = listed devices + `more`, tapping
opens the one device; ended ones staying until `dismissAt`, moved ones and unlisted ones cancelled; FCM
changes); notification-button presses (`PressReportTest`: the per-try times and budget, the press
report's JSON against the hub's schema, redacted URLs, where reports go, the queue limit, and against
MockWebServer the route order, a refused / 502 / answered try, a fresh connection per try and the
hub's own answer being final).

`./gradlew :phone:testDebugUnitTest` runs Compose semantics spot checks on the JVM (Robolectric,
`phone/src/test`): icon-only buttons have a label and a 48dp touch area, switches say what they switch and
their state, device tiles name their tap / hold actions, filters are selectable tabs, titles are headings,
and grid cells cap the font scale. (Robolectric downloads its Android jars on the first run.)

Read-only checks against the real hub (open endpoints only, never a command):

```sh
./gradlew :core:testDebugUnitTest --tests '*LiveHubTest' -Pnewman.liveHub=http://10.1.1.2:8093
```

On the NAS itself (container started with `--network host` and the hub's token file mounted at
`/hub-token`) the same test also reads devices, dashboards, activity, alerts and push categories:
`-Pnewman.liveHub=http://127.0.0.1:8093 -Pnewman.localTokenFile=/hub-token`.

## Push notifications (phone)

FCM is enabled only when `phone/google-services.json` exists; without it the app builds and runs,
just without notifications (the Me screen says so). To turn it on:

1. In the Firebase console, add an Android app with package `ca.nimaste.newman` (and
   `ca.nimaste.newman.dev` for debug builds) to the project whose service account the hub uses
   (`FCM_SERVICE_ACCOUNT_PATH`).
2. Download `google-services.json` and put it at `android/phone/google-services.json`
   (it is in `.gitignore`; never commit it). For the release workflow, store it base64-encoded as the
   GitHub secret `GOOGLE_SERVICES_JSON` (`base64 -w0 google-services.json`); without it the release's
   phone APK is built without push (the workflow warns).
3. The hub sends with that project's service account: `keys/firebase-service-account.json`, with
   `FCM_SERVICE_ACCOUNT_PATH` set (README → Settings).
4. Rebuild. The app registers with `POST /api/devices/register` after sign-in and whenever FCM
   rotates its token, and shows the hub's data-only messages itself: the snapshot picture, the
   channel (time-sensitive / doorbell / normal) and the action buttons from the payload (or from
   `GET /api/push/categories`). Plain buttons run straight from the notification shade: the
   receiver (`ActionReceiver`) only hands the press to **expedited WorkManager work** (`PressWorker`,
   `push/ButtonPress.kt`; a receiver may be stopped ~10 s after the tap, too soon for three tries).
   Out of expedited quota it runs as ordinary work; on Android 11 and older it runs as a short
   foreground service with a silent "Sending “Lock Laundry”…" notice on the Live status channel. The
   same button tapped twice while the first is still going runs once. The press is posted to
   `/api/public/actions` at the hub address in use, then the other address, and the payload's
   `action_url` last (`ActionRoutes`), the same way as the iPhone app: **each try on a fresh
   connection** (`PressProbe`: a client from the hub client's `newBuilder()` - same certificate,
   trust and Cloudflare headers - with its own empty `ConnectionPool(0, 1 s)` and dispatcher, so a
   press never waits behind the live socket, video or pictures), the first try up to 20 s, the others
   up to 8 s, 27 s in all (`PressTiming`, = iOS `PushPayload.attemptSeconds`). The hub answers at once
   and pushes back a command that fails later ("Couldn't lock Side Door"); its first own answer
   (done, or refused with its reason) is final. The notification shows the outcome. Sensitive ones
   (unlock, open, disarm) open the app and ask for the fingerprint / screen lock first, then run
   the same press from the app.

   **Press reports.** After every press the app sends `POST /api/public/press-report` (API.md; the
   iPhone app's field names, so the hub logs both phones alike): each try's start, URL (scheme, host,
   port, path only), process state (`appState`: foreground / fg service / visible / perceptible /
   service / background), time allowed, ms, HTTP status or the exception (`domain` = its class,
   `code` = the errno underneath, `underlying` = its causes, `path` = the network: "wifi validated",
   "cellular", "… blocked"), whether the client certificate was sent (`certChallenges` 1 /
   `certAvailable` true; `false` = TLS without a stored certificate) and OkHttp's event timings
   (`metrics`: dns, connect, TLS, request, response in ms after the call started, remote / local
   address, protocol, reused connection, proxy, cellular, metered, Data Saver). `launchState` is the
   process state at the tap ("cold" when Android started the app for the button) plus how long the
   work waited ("background +0.3s"); `sinceLaunchSeconds` the process's uptime; `backgroundExpired`
   that Android stopped the work; the summary says whether it ran as a foreground service or
   expedited. Successful presses carry the summary only. Reports are queued first (encrypted, at
   most 10), sent to all the hub's addresses at once (4 s each) and re-sent when the app comes to
   the foreground; the hub keeps a report once by its `id`.
   Tapping a camera alert's notification (it carries `clip` / `frigate_event_id`) finds that alert in
   `GET /api/alerts` (`CameraClip.matchAlert`) and opens its camera on it, over the Alerts tab; the
   camera screen plays an alert's video (`AlertVideo`): `api/clips/<integration>/<clip id>`
   (`CameraClip.clipPath`, encoded like the web's) plays in Media3 ExoPlayer (with its controls)
   through the hub client (certificate, Cloudflare headers, token, via `media3-datasource-okhttp`). A
   first `Range: bytes=0-1` request decides: a finished clip answers 206 (`CameraClip.parseContentRange`)
   and is streamed - it starts within moments and seeks with byte ranges; a clip still being recorded
   has no ranges on the hub, so that reply is the whole mp4, kept in the cache and played from the
   file. The snapshot and a spinner show until the video is ready to play, "The video isn't ready
   yet." + Retry when the hub has none or playback fails, and "Still recording" while the alert is
   ongoing. The player is released when the video goes away.

## Live statuses (phone)

The hub's live statuses (`server/newman/live.py`; a garage left open, an automation's "show a live status")
are **ongoing notifications** on the **Live status** channel (low importance: no sound, no vibration; updates
are silent): one per live status, keyed by its slot, showing its title, state and how long (a chronometer
from `since`), the devices on separate lines when there are several ("N more" adds "and N more"), and up to
three buttons from the devices' own actions. Plain buttons run straight from the shade (`LiveActionReceiver`
→ the device command, like a widget tap); sensitive ones (open, unlock, disarm) open the app and **always**
ask for the fingerprint / screen lock first (`BiometricGate`). Tapping one about a single device opens that
device. One that ended stays for two minutes with its final state ("Garage Door closed"), then goes.

Kept current (`LiveStatusNotifier`) from the app's live connection (`{"type": "live"}` — the whole list),
`GET /api/live/current` when the app opens, comes back to the front or reconnects, and — only when the build
has FCM — the hub's `{"type": "live"}` data messages, so they follow along with the app closed too. Without
FCM they follow while the app runs and catch up when it opens. Signing out removes them.

## Home-screen widgets and Quick Settings tiles (phone)

- **Three widgets** (Jetpack Glance 1.1, Newman's colours - the iPhone widgets' too), all resizable:
  - **Devices – tiles** (`DeviceGridReceiver`, the first widget's receiver, so widgets already placed
    keep working): large tiles - icon, name (16 sp bold), state (15 sp).
  - **Devices – rows** (`DeviceRowsReceiver`): one full-width row per device, name and state.
  - **Home status** (`HomeStatusReceiver`, `GET /api/widgets/home`): green "All secure" with a detail
    line ("2 locks · garage door closed") when every device whose kind has a secure state is at it;
    otherwise red, naming what isn't ("Side Door unlocked", "2 not secure") with a **Lock** / **Close**
    button (the kind's command that reaches its secure state). Wide (4x2) it adds the list (Locks: All
    locked / Garage door: Closed / Alarm: Disarmed, what isn't secure first) and up to two buttons.
    Worked out by the hub from kind metadata (`secure_state`, `is_mode`, `urgent`); nothing to choose.
    An older hub without it: "Update the Newman hub to see this".
- **Choosing devices** (tiles and rows): adding one opens the picker (`WidgetConfigActivity`): every
  device `GET /api/widgets/devices` offers, **by room** or **by type**, with search; tick up to 8 to add
  them, reorder or remove them under *On this widget*; and **Confirm first for sensitive actions** (off
  by default). Long-press the widget → reconfigure to change it. The widget shows as many as its
  current size takes (`WidgetLayout.maxDevices`: 2 small, 4 medium / narrow, 8 large; rows also no
  shorter than ~36 dp) and the picker marks the rest "Shown when the widget is bigger". The layout
  fills the widget and is worked out again on every resize: one device fills it (larger text); a
  square widget stacks up to 3, then goes two across (a short last line widens); a wide one puts them
  side by side. Short tiles put the icon beside the text; tall rows put the state under the name.
  The state's colour is its **tone** from `GET /api/widgets/state` (`good` green at its secure state,
  `active` amber when on or away from it, `alert` red for an urgent one like a leak, else grey; older
  hubs: amber when `active`).
- **Tapping** a device runs its primary action from `GET /api/widgets/state` (`actions[0]` with
  `primary`, worked out by the hub from kinds.py - the app has no list of kinds) through
  `POST /api/entities/{id}/command`, on the app's own OkHttp client (client certificate, Cloudflare
  headers, home or outside address). The tile shows "Unlock…" at once, then the hub's state 1.2 s and
  5 s later; a refusal shows "Couldn't unlock" until the next refresh. With *Confirm first* on, a
  sensitive action opens a small dialog (`ConfirmActivity`). Tapping a sensor (no action) opens the app
  on it.
- **Quick Settings tiles**: Newman 1, 2 and 3 (`QuickTiles`). Long-press a tile (`TileSettingsActivity`,
  the QS tile preferences screen) to pick its device; tapping an unset tile opens the same. The tile
  shows the device's name, its state as subtitle, active (highlighted) in the kind's active state,
  unavailable when offline; tapping runs the primary action - a sensitive one on a locked phone asks
  for the phone to be unlocked first (`unlockAndRun`).
- **Freshness** (`WidgetSync`): the last states and Home status are cached (plain SharedPreferences
  `newman_widgets`: device ids and states, no tokens), so widgets draw at once and while the hub is
  unreachable. They
  refresh on a tap, when the app's live WebSocket reports a change to a shown device (or reconnects)
  while the app runs, every 15 minutes through WorkManager (`WidgetRefreshWorker`, only while some
  widget or tile is set up, network required), and - only when the build has FCM - on the hub's
  `{"type": "widgets"}` data message (the FCM token is also sent to `POST /api/widgets/push-token`,
  kind `android`). Without FCM nothing else is needed. Signing out empties the cache (widgets then
  say "Sign in to Newman").

## Device Controls, shortcuts and Assistant (phone)

The Android counterparts of the iPhone's Control Center controls and Siri shortcuts:

- **Device Controls** (Android 11+, `NewmanControlsService`: the power menu or the Quick Settings *Device
  controls* panel; add Newman's from there). Offers every device `GET /api/widgets/devices` lists, grouped by
  room. A device with an on/off pair (two one-tap actions) is a **switch**, one with a single action or
  several (a button, a scene, a mode) a **button** running what a tap does now, a sensor shows its reading -
  all decided from the hub's actions in `GET /api/widgets/state`, never from a list of kinds. States refresh
  every 10 s while the panel is open, and at once from the app's live connection when it runs (`WidgetSync`).
  An action the hub marks **sensitive** (unlock, open, disarm) needs the phone unlocked first
  (`Control.setAuthRequired`); everything else works from the lock screen. Long-press a control to open its
  device's sheet in the app.
- **App icon shortcuts** (long-press the icon, `LauncherShortcuts`): the four devices run most from this phone
  (app taps, widgets, tiles, controls and shortcuts all count; kept on the phone only), each running what a
  tap does at that moment. Removed on sign-out; forgotten when changing to another hub.
- **Add to home screen** (a device's sheet, on launchers that pin shortcuts): a home-screen icon for that
  device doing the same. Both run through `ShortcutActivity`, a see-through activity that never opens the app:
  it fetches the device's current tap action, runs it and says so in a toast. A sensitive one asks for the
  lock screen first when the phone is locked (`KeyguardManager.requestDismissKeyguard`), else "Confirm it's
  you" when that's on in Me (`BiometricGate`).
- **No Google Assistant.** The phone app stays usable without Google services (GMS-free phones), so there
  are no App Actions / Assistant shortcuts; voice control is the wall tablet's.

## Accessibility (phone)

- **TalkBack**: device tiles read as one item with their state in the hub's own words ("Den plug, on",
  "Front door, locked, battery 80%"), and say what a tap and a hold do ("double-tap to turn off", "double-tap
  and hold to show all controls"); icon-only buttons have labels ("Next camera", "Update Sam's location");
  switches, filters, tabs and mode choices announce their role and state; sliders and the thermostat dial
  are adjustable (swipe up / down); page and section titles are headings; widget tiles, Quick Settings
  tiles and Device Controls carry the same "name, state" labels.
- **Touch targets**: at least 48dp everywhere (small-looking buttons get a bigger touch area).
- **Font size**: text follows Android's font size and wraps; fixed-size cells (the phone layout's square
  grid and the tab bar at 1.3x, half-width device cards at 1.2x; the People tile, fitted line by line, and
  compact tiles such as the small thermostat dial at 1x), so nothing is cut off.

## Tabs and screens (phone)

Four tabs: **Home · Cameras · Devices · More** (More's badge is the unseen alerts). The look is the approved
phone mockups' (solid cards, `H` colours and `HT` type in `ui/theme` and `ui/home/Kit.kt`). The phone no longer
reads `GET /api/phone-layout` or flows the dashboard; Home comes from `GET /api/home` (`HomeData`), reloaded on a
live `{"type":"home"}` or `{"type":"settings-house"}` and after reconnecting.

- **Home**: the hub's status - level ok: "All good at home" (green) with the hub's summary; warn: "Needs a look"
  (amber dot) with the summary; danger: one red banner per danger with its fix ("Close") - then each device warning
  in amber with **Mute** (the mute sheet); the person's
  cards in their order (titles, values and details worded by the hub, its tone colours them); up to four
  **favourites** (tap = the device's tap action, hold = its sheet). The pencil opens **Edit Home**: cards on/off,
  dragged into order (TalkBack: Move up / Move down), favourites removed or added from a device picker; Done saves
  `PUT /api/home/me`. Pull down to reload.
- **Climate** (the Climate card): the thermostat's dial (drag, or − / +), its modes, humidity and an auto range when
  it reports one; **Rooms** with each temperature on a bar over the house's range, coloured by its band
  (`HomeLogic.bandFor` / `barFraction`). **Edit**: every temperature sensor (`HomeLogic.climateSensors`) - tick,
  drag to order, tap to rename; Done saves `PUT /api/home/climate`.
- **Lights**: lighting-category devices that switch (kinds metadata) by `room` ("Other" last); a group
  (`group.<slug>`, `attrs.members`) is one row ("Group · 3 lights") with its lights behind "Show the 3 lights";
  a room's switch turns every row in it on or off; **All off**; a dimmable light that's on has its slider.
- **Security**: alarm panels' modes, locks and doors you control (next action, battery), sensors by kind
  (Doors, Water…), and today's events (`GET /api/alerts?category=security&since=`; older hubs: these devices'
  activity) with "All alerts".
- **Today** (`GET /api/today`): CALENDAR (today's events, or "Nothing on the Family calendar today"); TO DO (tap to
  tick done, Undo while just done; **+ Add** for admins - `POST /api/upkeep`, first due today); COMING UP (the
  calendars' next days, `attrs.upcoming`, and upkeep coming due with its status). **People**: everyone on one map (osmdroid) and the list (tap for their map).
- **Cameras**: the big picture - "Latest activity" or "Featured camera" (the hub's `cameras.mode`, set on the web)
  - then every other camera with its latest detection today; admins get **Change** (Latest activity / Featured
  camera + which one: `PATCH /api/settings/house {"cameras"}`). One camera: live (full screen a tap away), its
  detections (`GET /api/alerts?entity=&since=`; older hubs: the camera's `alerts` link) filtered by label, tap to
  play the clip in place; each "Earlier days" reaches back another 24 hours.
- **Devices**: every device by category, searchable, filters *All*, *Needs attention*, *On now*; a row opens its sheet.
- **More**: **Alerts**, **People**, **Automations**; **Edit Home**; the account (Me: look, location sharing,
  **Change password** - `POST /api/auth/password` - server, certificate, sign out).
  - **Automations**: most recently run first, each with its switch; **+ New** and tap-to-edit for accounts that
    may (`canAutomate`), read-only otherwise. The editor (`AutoEdit` works on the hub's own JSON, so whatever it
    doesn't know - notifications, wall, a newer trigger type - goes back unchanged and shows "Edit on the web"):
    name, on/off, the hub's summary (`POST /api/automations/describe`), **When** (a time with its days - shown
    S M T W T F S, saved 0 = Monday - a device changing, a device event, a reading above/below), **What happens**
    (device + command + value; brightness "then put it back after N min" as `for {amount, unit}`), **Only if**
    (device states; other limits kept), **Don't repeat for** (`cooldownMinutes`: Default = null, No wait = 0,
    1-60 min), **Run now** (not while there are unsaved changes), **Delete**. Back with unsaved changes asks first.
- **Notifications**: a tap opens the alert in the app at any age - the push's `alertId` (the run's id) finds the
  run's activity (`GET /api/events?runId=`): a camera clip plays on that camera's screen (Cameras tab); anything
  else opens More › Alerts scrolled to that entry, outlined. Pushes without one: matched by clip / review id. `"quiet": "1"` posts on the silent **Quiet hours** channel.

## People and location sharing (phone)

- **People tab**: everyone `GET /api/people` returns - "Home", a named place or "3.2 km away", and
  "updated 4 min ago" - with each person's **colour** (`color`) and **photo** (`photo`):
  `PersonAvatar` (rows, People tile, Me) loads the photo with Coil through the app's mTLS client,
  cached by its `?v=` URL, over their single initial on their colour (older hubs: a preset by
  position); the map pin draws the same. Tap someone for a full-screen **osmdroid** (OpenStreetMap)
  map: no API key, no Google needed, user agent `Newman-Android/<version> (ca.nimaste.newman…)`, tiles
  cached in the app's cache folder, "© OpenStreetMap contributors" on the map. An older hub (404)
  shows "Location sharing isn't set up on your hub yet".
- **Tap-to-refresh** (no live follow): tapping someone (or the refresh button on their map) posts
  `POST /api/people/{id}/refresh` and shows "Updating…" with a spinner until a live
  `{"type":"person"}` message (or a reload) brings a newer fix, or 45 s pass ("Couldn't reach their
  phone right now"). The hub allows one per 2 minutes per person; a 429 shows "Updated recently - try
  again in N s" briefly, a 404 (older hub) does nothing. The states are `PersonRefresh` in core
  (unit-tested). The list and map re-read the hub every 30 s otherwise - that never wakes a phone.
- **Me → How your family sees you**: twelve colour swatches (`PersonLook.PRESETS`, plus the current
  one if custom) saved with `PUT /api/me/profile {"color"}`; **Photo** via the system photo picker
  (`PickVisualMedia`, no storage permission), cropped to the centred square and scaled to at most
  1024 px on the phone, uploaded as JPEG to `PUT /api/me/photo` (the hub makes it 256 px); "Remove
  photo" is `DELETE /api/me/photo`.
- **Share my location** (Me): follows `GET /api/me/location` - hidden for "off", a switch for
  "choice" (`PUT`), on and locked ("Required by Chris") for "required". Turning it on explains, then
  asks for precise/approximate location, then (Android 10+) explains again and asks for "Allow all
  the time" (Android 11+ opens that as a settings page). Without background permission it still
  shares while the app is open.
- **Reporting** uses PendingIntent location updates (`LocationReceiver`), so no foreground service
  and no notification: the fused provider at balanced power (Wi-Fi/cell, about block level) every
  `everySeconds` (300) when Google Play services is on the phone, otherwise `LocationManager`
  (network, or passive). A fix is posted to `POST /api/me/location/report` when the interval is up
  or after a 75 m move (at most once a minute); the reply's `live` is ignored (there is no live
  mode), its `everySeconds` is kept. Battery: that balanced ~5 min / on-movement rate is all the
  phone ever does on its own. The hub's FCM data message `{"type":"locate"}` (someone tapped this
  person; at most once per 2 minutes) gets ONE high-accuracy fix, posted with `source: "refresh"`,
  and nothing more - a duplicate delivery within 30 s is ignored (`LocatePush.shouldAnswer`); it is
  silent, nothing is ever shown to the person being viewed. Updates restart after a reboot or app
  update (`LocationRestartReceiver`).
  Signing out stops everything.

## Release signing

Release builds are signed only when a keystore is configured, from environment variables or an
`android/keystore.properties` file (git-ignored):

| Environment | keystore.properties | |
|---|---|---|
| `ANDROID_KEYSTORE_PATH` | `storeFile` | path, relative to `android/` or absolute |
| `ANDROID_KEYSTORE_PASSWORD` | `storePassword` | |
| `ANDROID_KEY_ALIAS` | `keyAlias` | |
| `ANDROID_KEY_PASSWORD` | `keyPassword` | optional, defaults to the store password |

Without them `assembleRelease` produces unsigned APKs. Don't commit keystores or passwords.

**Release builds on GitHub** (`.github/workflows/release.yml`, on each `v*` tag): the shared keystore, key
alias `newman`, from two repository secrets — `ANDROID_KEYSTORE_BASE64` (`base64 -w0` of the `.jks`,
written to `android/release.jks`) and `ANDROID_KEYSTORE_PASSWORD` (the store password, also used for the
key) — plus `GOOGLE_SERVICES_JSON` for push (above). The version name comes from the tag (`v1.2.3` →
`1.2.3`, code `10203`); any other build is `dev`. The signed `Newman_<tag>.apk` and `NewmanWall_<tag>.apk`
are attached to the GitHub Release.

## The wall tablet (Amazon Fire)

Newman Wall is a full-screen WebView of `<server>/wall`; the tablet signs in inside that page.
It has no Google Play services or Firebase dependency (the build fails if one sneaks in), so it runs
on Fire OS 8 (Android 11).

### Sideloading onto a Fire tablet

1. On the tablet: **Settings → Device Options → About Fire Tablet**, tap the serial number seven
   times to enable **Developer options**; in **Developer options** turn on **USB debugging**
   (and **Stay awake** while charging, if you like).
2. Allow installing the app: **Settings → Security & Privacy → Apps from Unknown Sources** and
   allow the app you install with (Silk Browser / Files), or install over USB:
   `adb install -r wall-release.apk` (or `wall-debug.apk`).
3. Open **Newman Wall**. It starts on **Connect to your Newman** (see *Connecting* below): pick the hub
   found on the network, scan its QR code, or type its address; certificate and Cloudflare token are on
   the same screen. Then sign the tablet in. The sign-in page's **Server** button comes back to that
   screen. The kiosk options (dimming, brightness, pinning, device owner) are behind the top-left
   corner (hold 3 seconds, then the tablet's PIN / pattern if it has one), which also has **Change server…**.

### Kiosk mode: device owner (recommended) or screen pinning

As **device owner**, the app runs in lock-task mode: no status bar, no navigation, no way out,
and it becomes the tablet's home screen, so it comes back by itself after a restart. Set it up once
over USB:

```sh
adb shell dpm set-device-owner ca.nimaste.newman.wall/.WallAdminReceiver
# debug build:
adb shell dpm set-device-owner ca.nimaste.newman.wall.dev/ca.nimaste.newman.wall.WallAdminReceiver
```

**Caveat: this only works when there are no accounts on the device.** Android refuses
`set-device-owner` while any account exists ("Not allowed to set the device owner because there
are already some accounts on the device"). On a Fire tablet that includes the **Amazon account**,
so you have to deregister it first (**Settings → Your Accounts → Deregister**, or do it before
registering during setup), and possibly remove other accounts the tablet created. The trade-off:
without the Amazon account the tablet has no Amazon Appstore, Alexa, Kindle content or automatic
Amazon app updates (Fire OS system updates still install). You can register the Amazon account
again after `set-device-owner` succeeds - the restriction only applies at the moment you set it -
but Fire OS may then ask to re-add apps. A factory reset also clears device ownership.

To undo it: wall settings → **Stop being device owner** (then the app can be uninstalled), or a
factory reset. (`adb shell dpm remove-active-admin` doesn't work on a device owner unless the app
is a test-only build.)

Without device owner, the app uses **screen pinning** (turn it off in wall settings): each time the
wall comes to the front unpinned (e.g. after Back + Recents unpinned it and the wall was opened
again), it asks to pin once and Android shows its confirm prompt. A declined prompt waits for the
next time the wall is opened; it never asks right after the wall settings, nor after *Exit kiosk
until restart*. Pinning has to be enabled on the tablet (**Settings → Security → Screen
pinning**, where Fire OS offers it). To start after a reboot without device owner, either make
Newman Wall the default home app (if your Fire OS version allows it) or allow it to start itself:

```sh
adb shell appops set ca.nimaste.newman.wall SYSTEM_ALERT_WINDOW allow
```

### What the wall shell does

- **Kiosk**: immersive full screen (no system bars), screen kept on while showing.
- **Dims** after N minutes without a touch (default 5; 0 = never) to a low brightness (default 15%,
  never below 8% - `WallBrightness` in core - so a dimmed screen never looks off; older saved levels
  are raised to that). With Battery Saver on (it halves the window's brightness) the dimmed level is
  doubled back, and the wall logs a `NewmanWall` warning. The hub's page draws the dimmed screen
  itself (a dark screen, the time and date softly on it). The first touch only wakes it: full brightness
  is set on the touch itself, and the whole gesture goes no further (not to the page, not to the
  edges' system gestures). A hardware key or the screen turning on wakes it too. Brightness levels
  are in settings.
- **Shell colour**: the loading and "Can't reach Newman" screens use the page's theme colour (Glass's
  light lilac, Christmas's blue, Midnight's deep blue), reported with NewmanWall.setTheme and kept for the
  next start (WallShell in core).
- **Certificate**: the `.p12` imported in settings is offered to the hub only, through
  `onReceivedClientCertRequest`. **Cloudflare Access** headers are sent with the page load;
  Cloudflare then sets its `CF_Authorization` cookie, which the page's own requests and WebSockets
  carry.
- **Starts at boot** (`RECEIVE_BOOT_COMPLETED`), and recovers by itself if the page crashes or
  the hub is unreachable (retries every 15 s).
- **Battery reports** (`BatteryReporter`): `POST /api/tablet/battery` with level, charging, plugged
  (ac/usb/wireless/none), temperature and voltage from `ACTION_BATTERY_CHANGED`, plus `"screen"`,
  `"background": true` with `"nextReportIn"` (seconds) while the check-in alarm is armed, and
  `"dozeExempt"`, `"canComeBack"` (*Display over other apps* allowed, or device owner) and `"appVersion"`
  - while the screen is on: at start once the page is signed in, every 5 minutes,
  and at once when charging changes, the level moves 5 points or the screen comes back on. It uses the
  page's session (the hub's cookies from the WebView's `CookieManager`, or the tablet's token) over
  core's `HubHttp` client (certificate + Cloudflare headers); the request is `BatteryPost`'s. Failures
  are logged (`NewmanWall`) and retried 5 minutes later.
- **Battery with the screen off** (`BatteryAlarm`, `BatteryAlarmReceiver`, `BatteryWork`,
  `BatteryWorker`; timing in core's `CheckInTiming`). No foreground service, no notification.
  - **The check-in alarm** gets through Doze: `setExactAndAllowWhileIdle` every 15 minutes where exact
    alarms need no permission (below Android 12 - Fire OS 8 is Android 11; 12/13 get
    `SCHEDULE_EXACT_ALARM` at install, declared with `maxSdkVersion` 32), else `setAndAllowWhileIdle`
    every 10 minutes (up to 10 late). Android lets one through Doze at most every 9 minutes and gives the
    app ~10 s of network after it (`allow_while_idle_whitelist_duration`), so the receiver re-arms and
    then posts at once (`goAsync`, 8 s call timeout, one quick retry). Armed on every app start, boot,
    app update (`BootReceiver`), screen off, and each time it goes off. A force stop clears it until
    the app next starts; the report then leaves out `"background"`, so the hub doesn't wait for it.
  - **WorkManager** is the second path: every 15 minutes (network required; skipped while the screen is
    on and a report got through in the last 10 minutes) - but in Doze its job only runs in maintenance
    windows, hours apart overnight. It's enqueued with `UPDATE` on every app start, which hands it to
    JobScheduler afresh (on the Fire tablet a package update once left WorkManager believing its job
    was scheduled while JobScheduler had none). A one-off runs as soon as the screen goes off or, screen
    off, the charger is plugged in or out (a receiver registered while the process lives).
  - The hub shows *Screen off* (the tablet and its music player). Where a check-in should get through
    (charging or plugged in, or exempt from Doze) it calls it offline 35 minutes after the last report,
    or 20 minutes past an announced `nextReportIn`. On battery and not exempt, Doze may hold the
    check-ins back for hours: that's expected — still *Screen off*, no alert, offline only after 8
    hours without a word (and smart charging turns the charger on early if the last level was near the
    bottom of its band). With the wall coming back dimmed whenever the screen goes off over another
    app (above), the screen is normally never off long enough for this.
  - **Optional, if it still drops off overnight**: wall settings → *Battery reports while asleep* →
    *Let it report while asleep* asks Android to let the app ignore battery optimisation
    (`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`); or once over USB:
    `adb shell dumpsys deviceidle whitelist +ca.nimaste.newman.wall.dev` (the release build without `.dev`).
  - Checking it: `adb shell dumpsys alarm | grep -A4 BATTERY_CHECK_IN` shows the alarm and when it's
    next due. Trying Doze without waiting the night (screen off, unplugged): `adb shell dumpsys deviceidle
    force-idle`, wait for the next check-in, then `adb shell dumpsys deviceidle unforce`. The WorkManager
    job: `adb shell dumpsys jobscheduler | grep -B2 -A2 SystemJobService` for its id, then
    `adb shell cmd jobscheduler run -f ca.nimaste.newman.wall.dev <id>`.
- **`window.NewmanWall`** for the hub's page (only while the hub's own page is showing):

  ```js
  NewmanWall.version            // "1.2.3" (the release tag), "dev" / "dev-dev" for local builds
  NewmanWall.setBrightness(0.6) // 0..1, while awake
  NewmanWall.wake()             // wake the screen from dim (e.g. the doorbell rang)
  NewmanWall.dim()              // dim now (the page's dim button); the page draws the dimmed screen
  NewmanWall.isDimmed()         // true while dimmed (the page may load while it is)
  window.addEventListener('newmanwall-dim', (e) => { e.preventDefault(); /* e.detail.dimmed */ })
                                // dimmed / woken by the app; preventDefault() = the page draws it
  NewmanWall.keepAwake(false)   // let the screen sleep normally; true = keep it on (default)
  NewmanWall.openServer()       // "Connect to your Newman" (the sign-in page's Server button)
  NewmanWall.setTheme({ background: '#e6def5', scheme: 'light' })
                                // the page's theme colour (web/src/wall/theme.js SHELL), sent when the
                                // theme changes: kept, and painted behind the page while it loads, on
                                // "Can't reach Newman" and in the display cutout (Midnight until a page says)
  window.addEventListener('newmanwall', ...) // fired once the bridge is ready
  ```

- **Voice** (`VoiceListener`, see [Voice](#voice) below): `NewmanWall.voiceConfig(json)` and
  `NewmanWall.voiceDone()`; the app calls `window.NewmanVoice.event({...})` back.

### Voice

“Hey Newman” is heard **on the tablet** (README → *Voice on the wall tablet*):

- **Wake word:** openWakeWord's melspectrogram + embedding models and the custom `hey_newman` model,
  run with ONNX Runtime (`onnxruntime-android`, CPU, no Google services) on every 80 ms of audio
  (16 kHz mono, `VOICE_RECOGNITION` source, not marked privacy-sensitive). The models come from the
  hub (`GET /api/voice/wakeword` + its files, with the page's session like the battery reports) and are
  kept by version under the app's files, so a retrained model needs no new APK. A wake-up needs two
  frames in a row at the threshold (or one well over it), then 2 s of quiet.
- **After the wake word:** a short chime, the screen wakes, the page shows *Listening…* and the app
  records until 0.8 s of quiet after speech (energy against the room's noise level from just before
  the wake word, with a zero-crossing check; nobody speaking for 4 s = cancel; 8 s at most). The
  PCM goes to the page (`{type: "audio"}`), which sends it to the hub and plays the reply. The
  microphone is **released** from the end of the request until the page calls `voiceDone()` (the
  reply finished; 30 s at most).
- **Only in the foreground:** listening stops in `onPause` and starts again in `onResume`, and only
  while the signed-in page has turned voice on (`voiceConfig`). The microphone permission is asked
  for once (`RECORD_AUDIO`); a device-owner kiosk grants it to itself. Denied = no voice (allow it
  later in Android's app settings).
- **Sharing the microphone (Alexa on a Fire tablet):** Android 10+ gives the foreground app's
  recording priority and silences everyone else's, so with the wall listening “Alexa, …” would open
  Alexa but Alexa heard nothing. `AssistantWatch` watches every app's recordings
  (`AudioManager.registerAudioRecordingCallback`; ours are told apart by audio session): the moment
  another app starts recording — or Android reports our own capture silenced
  (`isClientSilenced`) — the wall lets go of the microphone; it listens again 1.5 s after the other
  app stops (silenced again and again: 3 s, 6 s… up to 30 s). Always-on wake-word detectors
  (`HOTWORD` source) are ignored, and a recorder that never stops is given up on after 3 minutes.
  Logged as `NewmanVoice`: *Recordings: …*, *Yielding the microphone: …*, *Microphone free again*.
- **Coming back after Alexa:** Fire OS's full-screen Alexa screen stays up until it's swiped away.
  Fire OS hides Alexa's own recording from other apps, so a takeover is told by how the wall left
  the front: another app's screen came over it — not the Home or Recents button (Android's
  `CLOSE_SYSTEM_DIALOGS` broadcast, reason `homekey` / `recentapps`, within 2 s), not a screen the
  wall opened itself (settings, PIN check, permission or pin prompt), not the screen turning off.
  (`onUserLeaveHint` can't tell: Android gives it for any app's activity started over the wall.)
  The wall then waits for the spoken answer to start (any playback that wasn't already playing
  when it left, `registerAudioPlaybackCallback` — Fire's Alexa answers on the music stream; Alexa+
  can take well over 6 s, and coming to the front over it cuts its speech off), then comes back
  once nothing else has played for 6 s (a follow-up answer starts that over), at least 4 s after
  leaving; if nothing plays within 30 s (a text-only answer) it comes back then; if another app
  plays for 20 s without a break
  (“Alexa, play some jazz”) it comes back anyway and the music carries on behind it; after 2 minutes
  it gives up. Logged at warning level as `NewmanVoice` *Return: …* (Fire OS hides info-level logs
  unless `adb shell setprop log.tag.NewmanVoice VERBOSE`). Wall settings → *Return to Newman after
  Alexa or another assistant* (on by default). **Once per tablet:** allow Android's **Display over
  other apps** for Newman Wall (the button under that setting, or
  `adb shell appops set ca.nimaste.newman.wall SYSTEM_ALERT_WINDOW allow`; `…wall.dev` for the debug
  build). Fire OS lacks Android's per-app overlay screen, so the button tries it, then the overlay
  list, then the app's details (moving on when a screen can't open or closes at once), and finally
  shows that adb command with the app's real package; the setting shows when it's allowed (checked
  on every return) — Android 10+ won't let an app come to the front by itself otherwise. A device-owner kiosk
  doesn't need it.
- **Coming back after leaving (any other way):** an accidental Home / Recents press, another app,
  an assistant the wall didn't come back from — `ComeBackWatch` brings the wall back once no Newman
  screen (the wall, its settings, Connect: from the app's activity lifecycle) has been in front for
  *N* minutes (wall settings: 1 / 2 / 5 / 10, default 2) **and** no other app has played anything for
  that long (any started playback from `registerAudioPlaybackCallback`: Prime Video, music, Alexa
  hold it off, and each playback that stops starts the wait over). Android gives an ordinary app no
  way to see touches in other apps or which app is in front without Usage Access, so silent use of
  another app (Settings, Silk) gets those minutes — turn the setting off for longer work. When the
  screen wakes (`SCREEN_ON` / `USER_PRESENT`) and the wall isn't in front, it comes back at once
  (unless something is playing). Wall settings → *Come back after leaving* and *Come back when the
  screen wakes* (both on by default; `come_back_after_leaving`, `come_back_minutes`, `come_back_on_wake`
  in `wall_settings`). Same *Display over other apps* need as above. Logged at warning level as
  `NewmanWall` *Come back: …*. The rule is `ComeBack` in `core/wall/` (`ComeBackTest`).
- **The screen never stays off:** while the wall is in front it holds the screen on (`FLAG_KEEP_SCREEN_ON`;
  dimming only lowers the window brightness, never below `WallBrightness.FLOOR`). Any other app in front
  (Alexa's screen, Silk, Settings) gets Fire OS's own screen timeout instead — and a dark tablet falls into
  deep sleep (Doze), where its battery check-ins don't get out. So whenever the screen goes off
  (`SCREEN_OFF`), for **any** reason — the power button with the wall in front too, a timeout over another
  app, Fire OS's charging / Show Mode screen — the wall comes back **at once**, whatever the settings above
  or anything playing (music carries on behind it): `bringWallBack` brings it to the front and turns the
  screen on with a short `SCREEN_DIM_WAKE_LOCK | ACQUIRE_CAUSES_WAKEUP` (plus the activity's
  `setTurnScreenOn` / `setShowWhenLocked`), straight into its **dimmed** screen (the date and time) rather
  than full brightness; a touch wakes it as usual. While the screen stays off it tries again every 30 s
  (`ComeBack.SCREEN_OFF_RETRY_MS`; a partial wake lock until then, so deep sleep doesn't hold the retry
  back); a return that worked resets that. The charger being plugged in or out (`POWER_CONNECTED` /
  `DISCONNECTED`, and again 10 s later) does the same, and over another app with the screen on brings the
  wall back at once (unless *Come back after leaving* is off or something plays). Every background
  check-in (the alarm, WorkManager) does too (`keepWallUp`): screen off → back on, dimmed; and if the
  wall's process was killed (the alarm starts it again), the wall is started — dimmed — unless someone is
  busy in another app with the screen on (`ComeBack.notRunning`). After a restart or an update
  `BootReceiver` starts it the same way. All of this needs *Display over other apps* (or device owner):
  each battery report says `canComeBack` (and the app's `appVersion`), and the hub shows "Allow 'Display
  over other apps' for Newman Wall so it can keep its screen on" on the tablet's health when it's
  missing. Only `keepAwake(false)` from the page lets the screen sleep normally.
- The pure parts (framing, levels, end of speech, the wake-score trigger, openWakeWord's buffering,
  the config, when to give up the microphone and when to come back) are in `:core` (`core/voice/`)
  with unit tests (`VoiceTest`, `MicShareTest`).
- APK size: ONNX Runtime adds ~27 MB (ARM only: `arm64-v8a`, `armeabi-v7a`).

**To check on a real tablet** (Samsung Galaxy Tab A11+ first; none of this can be tested here):

1. Wall settings → reload; sign in; the first time voice is on, Android asks for the microphone —
   allow it. Say “Hey Newman” from across the room at normal volume: chime + *Listening…* within
   about half a second. Try with the TV / music on, and that it ignores “Hey Norman”, “Newman”, “Hey
   Google” and a normal conversation over an evening (count false wake-ups; change *Wake
   sensitivity* in Household → Accounts → the tablet if needed).
2. **“Hey Google” while the wall listens:** with the wall on screen, say “Hey Google”. Android 10+
   lets the assistant's hotword run alongside another app's `VOICE_RECOGNITION` capture, and Samsung
   may detect it in its DSP — but if Google doesn't answer, check *Settings → Google → Hey Google*
   (or Samsung's Bixby “Hi Bixby”, which may need to be off) and report back; turning the wall's
   voice off for that tablet is the fallback.
3. During a reply nothing else is heard (the mic is released) and “Hey Google” should work then.
4. A timer that ends beeps and speaks every ~7 s; “Hey Newman, stop” must work over the beeps
   (they pause while it listens) — check from a few metres away.
5. Battery / heat: listening costs a little CPU all the time (ONNX on one core); check the tablet's
   battery temperature reading stays where it was after a day.
6. **Fire tablet with Alexa hands-free:** with the wall listening, “Alexa, what's the weather?” —
   Alexa should hear it the first time; once Alexa is done (about 6 s after it stops talking) the wall
   comes back by itself (but not after pressing Home or opening the wall settings); “Hey Newman” works again ~1.5 s after Alexa stops listening.
   `adb logcat -s NewmanVoice` shows each recording change, the yield and the return.
7. **Coming back after Home:** press Home; the wall is back after the set minutes (2 by default);
   turning the screen off and on brings it back at once; with Prime Video or music playing it stays
   away until that stops (plus the minutes). `adb logcat -s NewmanWall` shows *Come back: …*.
8. **Screen timing out over another app:** open Silk (or ask Alexa something that leaves its card up)
   and leave it: when Fire OS turns the screen off, it comes straight back on, dimmed, with the wall's
   date and time. Press the power button with the wall in front: it comes straight back on, dimmed.
   Unplug and plug the charger: the screen stays on with the wall in front.

## Connecting (both apps)

Neither app is tied to one house (no built-in address). With no hub saved, each starts on **Connect to
your Newman**, before sign-in:

- **On this network**: hubs announcing `_newman._tcp` (mDNS, found with Android's NsdManager - no Google
  services), listed by name and address; tap one.
- **Scan QR code**: ZXing (`com.journeyapps:zxing-android-embedded`, its own Camera1 screen; works on Fire
  OS). The code is the hub's `newman://connect?v=1&id=..&name=..&local=..&outside=..` link (Household →
  Accounts → Connect a device). The phone also opens that link directly; the wall's `ConnectActivity`
  takes it as an intent too.
- **Address**: typed, then **Test connection** (`GET <address>api/hello`). Problems in plain words: can't
  reach it; a TLS handshake failure or a proxy's 400/495/496 "certificate" answer → *needs a client
  certificate*; a redirect to `*.cloudflareaccess.com` (or a Cloudflare Access 401/403 page) → *needs a
  Cloudflare Access token*; an untrusted server certificate; an answer that isn't Newman's → *That isn't a
  Newman hub*.
- **Connection security** (same screen): the client certificate (`.p12` + password: validated on import,
  stored in EncryptedSharedPreferences, presented on every connection - API, live WebSocket, images,
  notification pictures and buttons on the phone, the WebView on the wall; it survives sign-out) and the
  optional Cloudflare Access service token (`CF-Access-Client-Id` / `-Secret`, sent to the hub on every
  request including WebSockets).

What's saved: the hub's id and name, its home address and its outside address (`ConnectionStore`).
**The phone**: `HubSelector` (`HubAddressPolicy`) keeps the address in use right: the home one whenever
`/api/hello` there answers with the same id (2.5 s probe). It moves to the outside one only after home has
failed for 2 minutes in a row - or at once after the phone's network changed - and only when the outside
one answers as the same hub, so a hub restart never moves it; while outside (or while home is failing) home
is probed every 30 s and wins as soon as it answers. Checked at start, on every network change and when the
app comes back to the front. The phone fills in a missing home address from the hub's own `/api/hello`.
**The wall** never switches by itself (`WallAddress`): it stays on the address chosen in Connect / Change
server and keeps retrying it through hub restarts and outages (installs from before the choice was kept:
the home address). Its native requests (battery reports) use the page's session cookie for that address,
else the tablet's token, which the app keeps from the page (`newman.tablet.token`) and hands to a page on
an address that has no sign-in yet; a failed report is logged (`NewmanWall`, with the HTTP status). Installs from before this keep their saved address (as the home address when it's
a home-network one, else as the outside one) and stay signed in.

The sign-in screens have a **Server** button back to this screen (the phone's own sign-in; the wall's
sign-in page through `NewmanWall.openServer()`).

**Changing server while signed in (phone)**: Me → **Server** opens the same Connect screen (so does a
`newman://connect` link opened while signed in, with that hub filled in). The same hub (same id, or an
address it shares when an older hub has none - `HubPlan`) just keeps its new addresses (**Save**); another
hub signs this phone out of the old one first (**Sign out and switch**: its session, push registration,
widget cache and shortcuts), saves the new hub and goes on to signing in there - as the iPhone's Me →
Server → Change does.

Plain `http://` is only accepted for home network addresses (10.x, 172.16-31.x, 192.168.x, `*.local`,
`*.lan`, `*.home.arpa`) - the apps check that themselves, so the network security config allows
cleartext in general (a hub's home address can be any private IP).
Fonts: Sora and Manrope (SIL Open Font License 1.1), bundled in `phone/src/main/res/font`.
