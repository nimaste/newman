# Newman apps — specification

Four apps, one behaviour: **Newman** (phone) and **Newman Wall** (tablet), each on iOS and Android.
Both platforms implement this document identically; where they differ it says so.

The hub (`server/`) is the source of truth for every request and reply. Read `server/newman/api.py`,
`server/newman/auth.py`, `server/newman/kinds.py` and `API.md`. Apps never hard-code kinds of device:
names, states, commands, value types and readings come from `GET /api/meta` (see `kinds.py`), so a
new kind of device appears in the apps with no app update.

## Identity

| | iOS | Android |
|---|---|---|
| Phone app | `ca.nimaste.newman` | `ca.nimaste.newman` |
| Phone notification extension | `ca.nimaste.newman.NotificationServiceExtension` | — |
| Phone location push extension (answers a tap-to-refresh while the app is closed) | `ca.nimaste.newman.LocationPushExtension` | — (an FCM data message reaches the app) |
| Phone widgets (home screen, Lock Screen, Control Center controls) | `ca.nimaste.newman.NewmanWidgets` | in the phone app itself: home-screen widgets (Devices – tiles, Devices – rows, Home status), Quick Settings tiles, Device Controls and shortcuts |
| Phone live statuses | Live Activities, drawn by `NewmanWidgets` (iOS 26) | ongoing notifications from the phone app |
| Wall app | `ca.nimaste.newman.wall` | `ca.nimaste.newman.wall` |
| App Group / shared Keychain | `group.ca.nimaste.newman` | — |
| Display names | "Newman", "Newman Wall" | same |

Apple team `T342YL2VCE`. Debug Android builds add `.dev`. The app's earlier working name must not appear anywhere.

## Project layout (in the repository root)

```
ios/        XcodeGen project.yml (iOS 17; NewmanWidgets iOS 26; Swift 5.9): targets Newman, NotificationServiceExtension, LocationPushExtension, NewmanWidgets, NewmanWall
            Shared/  code both apps use (API client, mTLS, auth, models, live connection)
android/    Gradle version catalog; modules :core (shared), :phone, :wall. compileSdk 35, minSdk 26, JVM 17
```

Follow the user's fleet conventions (GChat style): hand-written Info.plist via `INFOPLIST_FILE`
(XcodeGen's `info:` key clobbers it), `Config/Version.xcconfig`, Hilt + Retrofit/OkHttp +
kotlinx-serialization + Coil 3 on Android.

## Connecting

- **Server address.** No built-in address: with no hub saved the app starts on **Connect to your Newman**
  (before sign-in; the sign-in screen's **Server** button returns to it) - hubs found over mDNS
  (`_newman._tcp`), the hub's QR code (`newman://connect?v=1&id&name&local&outside`), or a typed address
  checked with `GET /api/hello` (README → Apps → Connecting the apps; API.md → Connecting an app). The app
  keeps the hub's home and outside addresses. The phone uses the home one while `/api/hello` there answers
  with the same id, and moves outside only after home failed for 2 minutes (or its network changed) and
  outside answers as the same hub. The wall stays on the address it was set up with (never switches). Plain http only for private addresses (checked by the app; cleartext allowed by the
  Android network security config / iOS ATS local-networking exception).
- **Client certificate (mTLS).** Import a `.p12` file + its password (file picker; iOS needs
  security-scoped access). Validate on import. Store the raw p12 (base64) + password:
  - iOS: Keychain, access group `group.ca.nimaste.newman`, `kSecAttrAccessibleAfterFirstUnlock`.
  - Android: EncryptedSharedPreferences.

  Present it on **every** connection — API, the live WebSocket, images, the wall WebView,
  notification images:
  - iOS: one shared `URLSession` delegate answering `NSURLAuthenticationMethodClientCertificate`.
  - iOS WKWebView: the same answer in `webView(_:didReceive:)`.
  - Android: an `X509ExtendedKeyManager` that re-reads the store on every handshake, used by every
    OkHttpClient.
  - Android WebView: `onReceivedClientCertRequest` → `proceed(privateKey, chain)`.

  The certificate survives sign-out. On the home network the proxy doesn't ask for one; that's
  expected.
- **Optional Cloudflare Access service token** (`CF-Access-Client-Id` / `CF-Access-Client-Secret`),
  sent alongside mTLS when set, on every request including WebSockets.
- **Sign-in.** `POST /api/auth/login {username, password, code?, kind, deviceName}`:
  - `kind` is `"app"` for the phone, `"tablet"` for the wall.
  - The reply carries `token`. Use it as `Authorization: Bearer <token>`. For the WebSocket, use
    `?token=` if headers can't be set.
  - Store the token in the Keychain / EncryptedSharedPreferences.
  - Handle the replies:
    - `401 {needs: "totp"}`: ask for the 6-digit code and resend with `code`.
    - `403 {error}`: show the error as-is.
- `GET /api/auth/state` reports `where` (home/away) and `certificate`. Show these on the account
  screen ("connected securely from outside" / "at home").

## Data

- **`GET /api/meta`:** kinds — states, commands (`valueType`: percent | number | state | choices |
  text, with min/max/step/unit/attr), events, properties, `sensitive` flags, categories.
- **`GET /api/entities`:** every device, with `stateLabel`, `properties`, `still`, `live`, `alerts`
  and `available`.
- **`GET /api/dashboards/current`:** the signed-in user's wall dashboard (the wall app). It has
  pages (tabs) of tiles, with tile types from `GET /api/dashboards/tiles`. Tiles carry `textSize`,
  and the dashboard has `names` (display names that override device names on this dashboard).
- **`GET /api/phone-layout`** (the phone app; 403 for a wall tablet's session) →
  `{"source": "default"|"own", "layout": Layout}` — the person's own layout, else the household
  default ([API.md](../API.md#phone-layouts)):

  ```
  Layout = {"today": bool,                 // Today card at the top of the first tab
            "pages": [{"id", "name", "icon", "tab": bool,
                       "tiles": [{"id", "type", "x": 0-3, "y": >=0, "w": 1-4, "h": 1-8,
                                  "config": {...}, "textSize": number|absent}]}]}
  ```

  - Each page is a vertically scrolling **4-column grid of square cells** (row height = column
    width); draw every tile exactly at its x / y / w / h. Tiles never overlap.
  - **Tabs:** the pages with `tab: true`, in order (at most 3), then **People** and **Alerts**.
    Pages with `tab: false` open from links at the bottom of the first tab; a dial tile may open the
    non-tab page holding a dial for the same device.
  - Tile types and `config` are exactly the dashboard tiles' (`GET /api/dashboards/tiles`). A
    devices tile draws its devices at phone size (the starter default gives each device its own
    1-row devices tile, a card).
  - The socket's `{type:"dashboard"}` (sent only to people whose layout changed) → reload
    `GET /api/phone-layout`. A hub without it answers 404: fall back to the dashboard.
- **`GET /api/ws`** (WebSocket) messages:
  - `{type:"entity", entity}`: one device changed; replace it.
  - `{type:"event"}`, `{type:"activity", entry}`.
  - `{type:"dashboard"}`: reload the dashboard (wall) / the phone layout (phone).
  - `{type:"settings"}`: reload sign-in settings.
  - `{type:"person", person}`: one person's location changed (same object as `GET /api/people`).

  Reconnect with backoff, and reload everything after reconnecting.
- **`POST /api/entities/{id}/command {command, value?}`.** Optimistic UI, rolled back on error; show
  the server's `error` text.
- **Pictures:** `GET /api/entities/{id}/still.jpg?h=<px>` for camera tiles (refresh about every 2 s
  while visible). Use `GET /api/entities/{id}/alerts` for a camera's events.
- **Alerts tab:** `GET /api/events` (activity) and `GET /api/alerts`.
- **Today:** `GET /api/today` (`server/newman/today.py`; on a phone: this person's items and
  everyone's) → `{todo, soon, events, headsUp}`. Actions: `POST /api/tasks/done`, `/api/tasks/undo`,
  `/api/tasks/snooze` with `{id}` (the To do item's id, a GCloud task; the hub records the
  signed-in person as who did it) and `POST /api/today/dismiss {id}` for an appliance's need.
- **People & location** (older hubs answer 404 to all of these; the apps stay quiet):
  - `GET /api/people` → `[{id, name, sharing, home, place|null, distanceM|null, lat|null, lon|null,
    accuracyM, at, live}]` (`live` is always false now); `POST /api/people/{id}/refresh` → 202
    `{requested, nextAllowedAt}` (someone tapped that person: their phones are asked for ONE fresh
    fix; at most once per 2 minutes per person, else 429 with `nextAllowedAt`); `GET /api/places` →
    `[{id, name, lat, lon, radiusM}]`. There is no live following: `POST /api/people/{id}/follow`
    answers 410.
  - `GET /api/me/location` → `{mode: "off"|"choice"|"required", enabled, requiredBy, everySeconds,
    live: false}`; `PUT /api/me/location {enabled}` (403 when required/off).
  - `POST /api/me/location/report {lat, lon, accuracyM|null, at (epoch s), speedMps|null, source:
    "background"|"refresh"|"app"}` → `{live: false, everySeconds: 300}`; 403 = not sharing (stop).
  - The one-fix ask (a "locate" push, one per refresh, no repeats): iPhone with a location push token
    (`POST /api/me/location/push-token`) → an APNs location push that starts LocationPushExtension even
    when the app was swiped away; other iPhones → silent
    `{"aps":{"content-available":1},"newman":{"type":"locate"}}`; Android → FCM data `{"type":"locate"}`.
    Either answers with exactly one report, `source: "refresh"`. Never shown to the person.

## Phone app (first release)

Matches the design boards (PhoneOverview, PhoneSecurity, PhoneLights, PhoneClimate, PhoneCamera,
PhoneAlerts, PhoneMe; dark "night glass" look: background `#0B1026` with soft blue and teal
glows, Sora for headings, Manrope for text).

- **Onboarding:** server address → optional certificate → optional CF token → sign in (2FA if
  asked).
- **Tabs (five):** the dashboard's first three pages (icon and name from the page - Overview,
  Security, Lights on the starter dashboard), then **People** and **Alerts**. A **Today page**
  (only greeting / To do / today / calendar / weather tiles) is not a tab: the first tab shows the
  phone's **Today card** at the top instead, with today's summary ("2 things to do · 1 event") under
  the greeting. Pages beyond the third (Climate) open from their compact thermostat tile, or from a
  link at the bottom of the first tab. A page's tiles flow into a **4-column** grid in dashboard
  order (wide tiles span 4, small ones 2). Every tile type renders generically from meta:
  - device toggles and status;
  - the round thermostat dial with its mode chips;
  - camera stills;
  - weather and calendar;
  - "needs attention";
  - To do / Today tiles as the Today card (the greeting tile isn't drawn: the header greets).
- **Today card:** To do (most urgent first, as the hub orders it) with **Done** (a GCloud task), a clock
  for **Remind tomorrow**, **Open** (a device needing a hand: its sheet) or Done for an appliance's
  need; a row just done stays struck through with **Undo** for 10 minutes. At most 4 rows, then
  "N more"; up to 3 of today's events and 2 heads-up. Reloaded when shown, every minute while shown
  and at most every 15 s after device changes. Hidden on hubs without `/api/today`.
- **People tab:** each person the hub returns: "Home" inside the home area, else the named place
  ("School") or the distance ("850 m away", "3.2 km away", "12 km away"), plus "updated 4 min ago".
  Tap someone (with a location) for a full-screen map of them: their dot, accuracy circle, name,
  "Home"/place/distance and update time. **Tap-to-refresh:** the tap (or the map's refresh button)
  sends `POST /api/people/{id}/refresh` once; "Updating…" shows until a `person` message with a newer
  fix arrives, or 45 s pass ("Couldn't reach their phone right now"); a 429 says quietly that it was
  updated recently; a 404 (older hub) does nothing. Live `person` messages move them; `GET
  /api/people` every 30 s as a fallback (asks no phone for anything). iOS: MapKit. Android: osmdroid (OpenStreetMap, no key, no Google; app user agent,
  "© OpenStreetMap contributors"). On a 404: "Location sharing isn't set up on your hub yet".
- **Share my location (Me):** from `GET /api/me/location`: "off" → hidden; "choice" → a switch
  (`PUT`); "required" → on and locked, "Required by Chris". Turning it on explains in plain words
  ("Your household requires location sharing" for required accounts), then asks: iOS "While Using
  the App" then "Always"; Android fine/coarse, then (10+) background ("Allow all the time", a
  settings page on 11+). Without background permission the phone shares while the app is open.
- **Location reports:** battery-cheap in the background, posted to `/api/me/location/report` when
  `everySeconds` is up or after a 75 m move (at most once a minute). iOS: significant-location
  changes + visits, `showsBackgroundLocationIndicator = false` (no blue bar with "Always").
  Android: PendingIntent updates (no foreground service, no notification) from the fused provider
  at balanced power when Google Play services is present, else LocationManager network/passive.
  There is no live mode (it drained batteries; `ReportSchedule` is shared and tested on both
  platforms). A `locate` push (someone tapped this person) takes ONE fresh fix, posts it with
  `source: "refresh"` and stops. iOS: the LocationPushExtension answers the APNs location push
  (`startMonitoringLocationPushes`; its token goes to `POST /api/me/location/push-token`), the app
  itself the silent push. Android: the FCM data message, a single current-location fix. The person
  being viewed is never told.
- **Device detail sheet:** state, every reading (`properties`), and every command, using the
  control its `valueType` asks for (slider, stepper, state picker, choice chips, text).
- **Sensitive commands** (`sensitive: true` in `/api/meta` — unlock, disarm, open the garage, sound
  a siren) need Face ID / Touch ID / Android BiometricPrompt, falling back to the device passcode,
  before sending - from tiles, the device sheet, the dial and notification buttons alike. A switch
  in Me turns it off (on by default); a phone with no passcode at all has nothing to ask.
- **Live camera video** (like the wall): the app opens the hub's live socket itself
  (`entity.live` + `?quality=sub` for tiles, `main` full screen) with its certificate and Bearer
  token, and feeds it to the shared player page `mobile-shared/live-player.html` in a web view. The
  protocol is documented at the top of that file. The still picture shows until video plays, and
  again if it fails. At most 4 tiles stream at once; off-screen tiles and a backgrounded app stop
  streaming.
- **Camera full screen:** live video (`main`) plus today's events for that camera.
- **Account ("Me"):**
  - name, home/away and certificate status, and the server;
  - the certificate (import / replace / remove);
  - the biometrics switch;
  - "Share my location" (see above);
  - sign out of this phone (stops location sharing on this phone).
- **Push:**
  - Register with `POST /api/devices/register` (read the handler for the fields).
  - iOS: APNs; a NotificationServiceExtension attaches `snapshot_url` images using the shared
    Keychain certificate (only snapshot pushes carry `mutable-content`). It must never register
    notification categories (on iOS 26 that stopped every later notification until a restart): the
    app registers every set from `GET /api/push/categories` at launch, on coming to the foreground and
    on background wakes (silent push, location event; at most every 15 minutes).
  - Android: FCM, enabled only when `android/phone/google-services.json` exists (apply the plugin
    conditionally, so builds work without it).
- **People** (location sharing): the hub side — sharing modes, reports, tap-to-refresh, the
  `locate` pushes, places — is specified in [API.md → Where people are](../API.md#where-people-are).
- **iOS background modes:** `remote-notification` (the silent `locate` push) and `location`; usage
  strings `NSLocationWhenInUseUsageDescription` and `NSLocationAlwaysAndWhenInUseUsageDescription`.
  No new App ID capability is needed.
- **Not yet:** voice.

## Wall app (first release)

A native shell around the hub's own wall screen (`<server>/wall`); the tablet signs in inside that
page as it does in a browser.

- **Full-screen kiosk:** no system bars; the screen stays on while it's showing.
- **Dims after N minutes without a touch** (setting; default 5) to a low brightness (default 15%,
  never below 8% so it never looks off), and the first touch wakes it at once (that touch does nothing else).
- **Starts at boot (Android).** Uses lock-task mode when the app is the device owner (set up once
  over USB; document the `adb` command in the README), otherwise screen pinning.
- **iOS:** document Guided Access.
- **Must run without Google Play services:** Amazon Fire tablets (Fire OS 8, Android 11) are the
  first target. No FCM, no Google libraries in the wall app.
- **Certificate and Cloudflare headers** as above, handled in the WebView.
- **Settings screen** (behind a long-press in a corner + device PIN or biometrics): server address,
  certificate, dim delay, brightness levels, reload.
- **JavaScript bridge** `window.NewmanWall` (Android answers only while the hub's own page is
  showing; iOS injects it into the main frame and takes the voice calls only from the hub's page), with
  `native: true` (a real shell, not a page stand-in) and a `newmanwall` window event once it's there:
  - both shells: `version`, `setBrightness(0..1)`, `wake()`, `dim()` (dim now), `isDimmed()`,
    `keepAwake(bool)`, `openSettings()` (this app's settings, after the PIN / Face ID), `openServer()`
    ("Connect to your Newman"), `setTheme({background, scheme})` (the shell's colour while loading),
    `voiceConfig(json)` (`{"enabled", "threshold", "version"}`: listen for "Hey Newman" or not),
    `voiceDone()` (the spoken reply finished: listen again), `recordClip(ms, fixed)` / `stopClip()`
    ("Teach Hey Newman": one clip, answered through `window.NewmanVoice.event`); on dimming and waking
    the app tells the page with a cancelable `newmanwall-dim` event (`detail.dimmed`) and darkens the
    screen itself unless the page cancels it;
  - iOS only: `platform: "ios"`.

  The hub's wall alerts use `wake()` (e.g. the doorbell's camera full screen).
- **Battery reports.** `POST /api/tablet/battery {level, charging, plugged, temperatureC, voltageMv,
  screen, background?, nextReportIn?, dozeExempt?, canComeBack?, appVersion?}` (204) with the wall page's own session (its cookies
  for the hub, read from the web view, else the tablet's token) and the usual certificate / Cloudflare
  headers: when the wall starts and is signed in, every 5 minutes while the screen is on, and at once when
  charging starts or stops or the level moves 5 points. Signed out, 401, 404 (older hub) or no network:
  skipped quietly, tried again 5 minutes later; never shown. Android reads `ACTION_BATTERY_CHANGED`
  (plugged ac/usb/wireless, temperature, voltage) and keeps reporting with the screen off — a while-idle
  alarm every 15 minutes, even in Doze, with `background: true` and `nextReportIn`, plus WorkManager —
  and its settings' *Battery reports while asleep* section can ask to ignore battery optimisation. iOS
  has only level and state, so `plugged` is `"ac"` while charging/full and temperature/voltage are
  `null`; it sends `screen: "off"` as it goes to sleep and nothing more until woken. The hub shows a
  sleeping tablet (and its music player) as *Screen off*, not offline (API.md → Phone layouts → tablet battery).

- **Voice (Android and the iPad wall).** Listens for “Hey Newman” on the tablet while the wall is on
  screen (openWakeWord models from `GET /api/voice/wakeword`, ONNX Runtime — `onnxruntime-android`,
  and the same version's Swift package on iOS, NewmanWall only), records what's said until the
  speaker stops and hands it to the page (`window.NewmanVoice.event`), which sends it to the hub
  (`POST /api/voice/utterance`) and plays the reply. Microphone asked for once; released while the
  reply plays. Same rules and page events on both (iOS: `VoiceListener.swift` mirrors
  `VoiceListener.kt`, with the microphone from an AVAudioEngine tap).

## Builds

- **Android:** builds on the NAS in Docker (`./gradlew :phone:assembleDebug :wall:assembleDebug`,
  plus unit tests for `:core`). Release signing comes from env/`keystore.properties` (not in the
  repository); release APKs are built and signed (shared keystore, alias `newman`) by
  `.github/workflows/release.yml` on each `v*` tag, with `google-services.json` from a secret.
- **iOS:** generated with XcodeGen and built on GitHub Actions (`macos-latest`) as Ad Hoc IPAs, like
  the user's other apps (`.github/workflows/release.yml`, five Ad Hoc profiles). Locally only syntax
  can be checked.
