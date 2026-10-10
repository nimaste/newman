# Newman for iOS

Two apps, built from one XcodeGen project (`project.yml`), following `docs/apps.md`:

| Target | Bundle id | What it is |
|---|---|---|
| `Newman` | `ca.nimaste.newman` | The phone app: Overview / Security / Lights / People / Alerts, Today, device controls, cameras, push, location sharing |
| `NotificationServiceExtension` | `ca.nimaste.newman.NotificationServiceExtension` | Registers each push's own buttons as its notification category (so buttons show even for a set the app hasn't fetched yet) and attaches camera snapshots (`snapshot_url`), using the client certificate from the shared Keychain |
| `LocationPushExtension` | `ca.nimaste.newman.LocationPushExtension` | Apple's Location Push Service Extension: answers the hub's one-shot location push (someone tapped this person on their map) with ONE fix, even when the app was swiped away |
| `NewmanWidgets` | `ca.nimaste.newman.NewmanWidgets` | WidgetKit extension (phone): home-screen device widgets, Lock Screen device widgets, Control Center controls, widget push updates, live status Live Activities (iOS 26 only) |
| `NewmanWall` | `ca.nimaste.newman.wall` | The wall tablet: a full-screen kiosk around the hub's own `<server>/wall` page |

iOS 17+, Swift 5.9. One Swift package, for the wall app only: ONNX Runtime (`microsoft/onnxruntime-swift-package-manager`, pinned to 1.20.0 like the Android wall's `onnxruntime-android`) for the on-device wake word. Team `T342YL2VCE`, automatic signing for local builds.

```
ios/
  project.yml                 XcodeGen spec (hand-written Info.plists via INFOPLIST_FILE)
  Config/                     Version.xcconfig + sync-version.sh (runs before every xcodegen generate)
  Shared/
    Core/                     Pure Foundation: models, JSON, tile flow, controls, protocol (tested on Linux)
    Platform/                 Keychain, client certificate, mTLS URLSession, API client, WebSocket, Face ID,
                              hub discovery (Bonjour), /api/hello probe, home/away address choice
    Widgets/                  App Intents shared by the app and NewmanWidgets: device picker (DeviceQuery),
                              widget / control / Siri actions, WidgetHub (hub calls + App Group caches)
    UI/                       Night-glass theme, SF Symbol names, hub images, certificate import,
                              the Server screen and QR scanner (phone and wall)
    Resources/Fonts/          Sora + Manrope (Google Fonts, SIL OFL - licences alongside)
  Newman/                     Phone app (App/, Views/, Resources/)
  NotificationServiceExtension/
  LocationPushExtension/      LocationPushService (Info.plist: com.apple.location.push.service)
  NewmanWidgets/              Widgets, Lock Screen widgets, controls (Info.plist: com.apple.widgetkit-extension)
  NewmanWall/                 Wall app (Voice/: "Hey Newman" - microphone, ONNX wake word, models, Teach clips)
  Tests/                      Linux checks: Fixtures/ (JSON captured read-only from the hub) + LinuxRunner/
```

## Build on a Mac

```sh
brew install xcodegen
cd ios
xcodegen generate          # also runs Config/sync-version.sh
open Newman.xcodeproj      # schemes: Newman (phone + its extensions), NewmanWall
```

**After pulling the widgets** (new target `NewmanWidgets`, new folders `NewmanWidgets/` and
`Shared/Widgets/`): run `xcodegen generate` again. In Xcode, select the `NewmanWidgets` target →
Signing & Capabilities: your team, automatic signing, and **App Groups** (`group.ca.nimaste.newman`),
**Keychain Sharing** and **Push Notifications** should all show (they come from
`NewmanWidgets/NewmanWidgets.entitlements`). The first build creates the
`ca.nimaste.newman.NewmanWidgets` App ID and its development profile; if Xcode says the App Group or
Push Notifications isn't enabled for it, tick them on that App ID in the portal (below) and build
again. The widget extension is embedded in the phone app (scheme **Newman**) - there is nothing to
install separately. The widget extension targets **iOS 26** (Xcode 26+): every widget takes WidgetKit
push updates and the controls are always in. The phone app itself, its other extensions and the
wall stay on iOS 17; on an older iPhone the app runs without widgets.

Run `xcodegen generate` again after pulling changes that add files or Info keys (the connect-first
setup added Swift files, the `newman` URL scheme, `_newman._tcp` in `NSBonjourServices` and
`NSCameraUsageDescription` - in both `Newman/Resources/Info.plist` and `NewmanWall/Resources/Info.plist`; no new capabilities or
entitlements). Pick your team's automatic signing and run on a device (push needs a real device). Set the version
in Xcode's General tab as usual; `sync-version.sh` keeps it across regenerations. `Newman.xcodeproj`
is generated and ignored by git.

Without a Mac, `Tests/run-linux-checks.sh` (Docker, `swift:6.0`) parses every Swift file and
type-checks + runs the pure core against the captured hub JSON. SwiftUI/UIKit code can only be
compiled on macOS - that's what `.github/workflows/ios-build.yml` does on every push.

## Apple Developer setup (once)

In [developer.apple.com](https://developer.apple.com/account) → Certificates, Identifiers & Profiles:

1. **App Group** - Identifiers → App Groups → `group.ca.nimaste.newman`.
2. **App IDs** (explicit):
   - `ca.nimaste.newman` - capabilities **Push Notifications**, **App Groups** (`group.ca.nimaste.newman`)
     and **Location Push Service Extension** (granted by Apple on request; enabled - done).
   - `ca.nimaste.newman.NotificationServiceExtension` - **App Groups** (`group.ca.nimaste.newman`).
   - `ca.nimaste.newman.LocationPushExtension` - **App Groups** (`group.ca.nimaste.newman`). Xcode's
     automatic signing creates this App ID and its development profile on the first build.
   - `ca.nimaste.newman.NewmanWidgets` - **App Groups** (`group.ca.nimaste.newman`) and **Push
     Notifications** (WidgetKit push updates use the widget extension's own token). Automatic signing
     creates this App ID on the first build; check both capabilities are ticked on it.
   - `ca.nimaste.newman.wall` - no extra capabilities.

   Keychain Sharing needs no portal setting: the apps use `$(AppIdentifierPrefix)group.ca.nimaste.newman`
   and the App Group as their keychain access group. Location sharing needs nothing in the portal
   either: Background Modes (`location`, `remote-notification`) and the location usage strings live
   in `Newman/Resources/Info.plist`, and the silent "locate" push uses the existing Push
   Notifications capability. Location pushes need the **Location Push Service Extension**
   capability on `ca.nimaste.newman` (above): the entitlement `com.apple.developer.location.push`
   is in the **app's** entitlements only - the app calls `startMonitoringLocationPushes` and owns the
   token; the extension just needs the App Group / shared Keychain (like the notification extension).
   The hub sends them with the same APNs key (token auth is required for location pushes).
3. **APNs key for the hub** - Keys → a key with *Apple Push Notifications service*. Download the `.p8`
   and give the hub `APNS_KEY_PATH`, `APNS_KEY_ID`, `APNS_TEAM_ID=T342YL2VCE`,
   `APNS_BUNDLE_ID=ca.nimaste.newman` (see `.env.example`). Ad Hoc builds use **production** APNs;
   debug builds from Xcode register as `apnsEnvironment: "sandbox"`, which the hub honours per phone.
4. **Devices** - register the UDIDs of every iPhone/iPad that will install the Ad Hoc builds.
5. **Distribution certificate** - an *Apple Distribution* certificate; export it from Keychain Access
   as a `.p12` with a password.
6. **Five Ad Hoc provisioning profiles**, each including those devices:
   - "Newman Ad Hoc" → `ca.nimaste.newman` (regenerate it after enabling Location Push Service
     Extension, so it carries `com.apple.developer.location.push`)
   - "Newman Notifications Ad Hoc" → `ca.nimaste.newman.NotificationServiceExtension`
   - "Newman Location Ad Hoc" → `ca.nimaste.newman.LocationPushExtension`
   - "Newman Widgets Ad Hoc" → `ca.nimaste.newman.NewmanWidgets`
   - "Newman Wall Ad Hoc" → `ca.nimaste.newman.wall`

   Regenerate and re-upload them (secrets below) whenever you add a device.

## GitHub Actions

- `.github/workflows/ios-build.yml` - every push touching `ios/`: `xcodegen generate` and an
  unsigned simulator build of both schemes (`CODE_SIGNING_ALLOWED=NO`).
- `.github/workflows/release.yml` - on a `v*` tag (or run by hand with a tag): archives and exports
  Ad Hoc IPAs for the phone app (with its notification, location push and widget extensions) and the wall app, attached to the GitHub
  Release as `Newman_<tag>.ipa` and `NewmanWall_<tag>.ipa`.

Repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `IOS_DISTRIBUTION_CERT_BASE64` | `base64 -i distribution.p12` |
| `IOS_CERT_PASSWORD` | the `.p12` password |
| `IOS_KEYCHAIN_PASSWORD` | any random string (the CI's temporary keychain) |
| `IOS_PROVISIONING_PROFILE_BASE64` | `base64 -i Newman_Ad_Hoc.mobileprovision` |
| `IOS_PROVISIONING_PROFILE_NOTIFICATION_BASE64` | `base64 -i Newman_Notifications_Ad_Hoc.mobileprovision` |
| `IOS_PROVISIONING_PROFILE_LOCATION_BASE64` | `base64 -i Newman_Location_Ad_Hoc.mobileprovision` (the release fails clearly without it) |
| `IOS_PROVISIONING_PROFILE_WIDGETS_BASE64` | `base64 -i Newman_Widgets_Ad_Hoc.mobileprovision` (the release fails clearly without it) |
| `IOS_PROVISIONING_PROFILE_WALL_BASE64` | `base64 -i Newman_Wall_Ad_Hoc.mobileprovision` |

Each target takes its own profile: `project.yml` sets `PROVISIONING_PROFILE_SPECIFIER` to
`$(NEWMAN_PROFILE_PHONE)`, `$(NEWMAN_PROFILE_EXTENSION)`, `$(NEWMAN_PROFILE_LOCATION)`,
`$(NEWMAN_PROFILE_WIDGETS)` and `$(NEWMAN_PROFILE_WALL)`, which are empty locally (automatic
signing) and set to the installed profiles' UUIDs by the release workflow (which also maps each
bundle id to its profile in the export options).

Release: `git tag -a v1.0.0 -m "What changed" && git push origin v1.0.0`.

## How the apps reach the hub

- **Connect first (phone)** - no address is built in. With no saved hub the app opens on
  **Connect to your Newman** (`ServerSetupView`), before sign-in; the sign-in screen's **Server**
  button and **Me → Server → Change** open the same screen. Ways in: hubs found on this network
  (Bonjour `_newman._tcp`, TXT `id`/`name`/`v`/`ext`/`path`, resolved to `http://host:port`), **Scan
  QR code** (AVFoundation) or a `newman://connect?v=1&id=…&name=…&local=…&outside=…` link
  (`.onOpenURL`), or a typed address. **Connection security** (client certificate, Cloudflare Access
  token) lives there too, so it works without signing in. **Test connection** calls the open
  `GET /api/hello` and explains failures in plain words (can't reach it, needs a client certificate,
  needs a Cloudflare Access token, isn't a Newman hub). Saving keeps the hub's `id`, `name`, home and
  outside addresses in the shared Keychain (`ConnectionStore.hub`); a hub from before `/api/hello`
  is kept as a plain address.
- **Home or away (phone only)** (`HubRoute`, rules in `HubRoutePolicy` in Shared/Core, checked on
  Linux) - at launch, on returning to the foreground and on network changes (`NWPathMonitor`) the
  phone asks the home address's `/api/hello` (2.5 s). A hub restart must not move it outside, so it
  switches home → outside only when home has failed **continuously for 2 minutes** (re-probed every
  15 s while the app is open; a failure long after the previous one restarts the clock) **or** the
  network changed (Wi-Fi ↔ cellular, another Wi-Fi: interface + gateways differ from where home last
  answered), **and** the outside address's `/api/hello` answers with the same hub id. While outside it
  probes home every 30 s (app open) and switches back as soon as home answers. The choice is
  `ConnectionStore.serverURL`, which the API, live socket and extensions read. The location push
  extension and the widgets retry the hub's other
  address when the one in use can't be reached. Installs that saved only an address keep it and stay
  signed in; the app learns the hub's id and other address from it in the background. A private
  address such as `http://10.1.1.2:8093` works at home (`NSAllowsLocalNetworking`); iOS 18 asks for
  Local Network access (the app browses Bonjour at launch so the prompt appears).
- **Connect first (wall)** - the wall uses the same Server screen but **never switches address by
  itself**: it stays on the address that answered on the Server screen (Connect / Change server) and
  keeps retrying it every 30 s, so a hub restart can't move it to the outside address and break the
  page's session. With no
  saved hub it opens on **Connect to your Newman**; while the wall page shows its own sign-in (no
  `newman_session` cookie for the address in use) a **Server** button sits bottom-right, and the
  can't-reach screen and the hidden settings (**Change server…**) open it too. The page's session
  cookie belongs to one address, so the page asks for a sign-in once more after you change the
  wall to the other address on the Server screen. The QR scanner uses the iPad's front camera.
- **Client certificate (mTLS)** - import a `.p12` and its password (Files picker). It's validated
  with `SecPKCS12Import`, then the raw p12 (base64) and password are kept in the Keychain
  (`kSecAttrAccessibleAfterFirstUnlock`; on the phone in the shared group `group.ca.nimaste.newman`
  so the notification extension can use it). One `URLSession` delegate answers
  `NSURLAuthenticationMethodClientCertificate` for the API, the live WebSocket and every picture
  (no `AsyncImage`); the wall's `WKWebView` answers the same challenge in
  `webView(_:didReceive:completionHandler:)`. The certificate survives sign-out.
- **Cloudflare Access (optional)** - `CF-Access-Client-Id` / `CF-Access-Client-Secret` on every
  request, including the WebSocket and notification images. In the wall's web view, custom headers
  only reach the main page load, so the app first calls the hub once with the headers through its
  own session and copies Cloudflare's `CF_Authorization` cookie into the web view; scripts, pictures
  and the page's WebSocket then carry the cookie. If that cookie expires mid-session, sub-requests
  fail until the page reloads (the wall reloads after any failed load, and "Reload" in settings
  renews it).
- **Sign-in (phone)** - `POST /api/auth/login` with `kind: "app"`; a `401 {needs: "totp"}` asks for
  the 6-digit code and resends; a `403 {error}` is shown as-is. The token is kept in the Keychain and
  sent as `Authorization: Bearer`. The wall signs in inside its page, like a browser.

## Phone notes

- Phone layouts (`GET /api/phone-layout`, `PhoneGrid` in Shared/Core, `PlacedGrid.swift`): the hub
  sends the signed-in person's layout (their own, or the house default). Pages marked `tab` are the
  tabs, in order, at most three, then **People** and **Alerts**; other pages are links at the bottom
  of the first tab and open from their thermostat tile. Each page is a scrolling grid of 4 columns
  of square cells with tiles exactly at their `x`/`y`/`w`/`h` (no reflow, empty cells stay empty),
  drawn by the usual tile views sized to their rectangle (a tile with more than fits scrolls inside
  it). A **devices** tile shows its devices inside its rectangle: two columns of buttons when wide
  enough (240 pt), a list when narrow (tap = main action, long press = everything); what doesn't
  fit becomes "+N more", which opens the full list. A **People** tile shows the People tab's rows
  (initial, name, "Home" / a place / "3.2 km away", "updated 4 min ago"; "Not sharing location"
  dimmed and not tappable), only the tile's `people` when it names some, as many as fit
  (`PeopleTileLogic`, `TileFit`) with the rest as "+N more" (opens the People tab), or one line when
  too short for rows; tap someone for their map in the tile's `mapLook` (light by default, or dark).
  It uses the People tab's data and live `{"type":"person"}` messages - no polling of its own. A
  **Greeting** tile says "Good morning / afternoon / evening" over today's line from `GET /api/today`
  ("3 things to do · 2 events today · rain from 3 PM", from the heads-up `short` words), sized to its
  rectangle and on one line when it's too short for two. The Today card shows on the first tab only when
  the layout's `today` is on. A live `{"type":"dashboard"}` reloads the layout. Nothing is cached:
  the layout loads with the rest of the house. Older hubs (404) get the flowed dashboard below.
- Tabs on older hubs: the dashboard's first three pages (icon + name; Overview, Security, Lights on the starter
  dashboard), **People** and **Alerts** (`PhoneTabs` in Shared/Core). A Today page (greeting / To do /
  calendar / weather) isn't a tab: the first tab shows the Today card (`GET /api/today`: To do with
  Done / Remind tomorrow / Open, today's events, heads-up) and today's summary under the greeting.
  Pages beyond the third (Climate) open from their thermostat tile, or from a link at the bottom of
  the first tab. Tiles flow into a 4-column grid in the
  dashboard's reading order (top to bottom, left to right): wide tiles (5+ of the wall's 12 columns,
  and lists) span 4, small ones 2, and a half-width tile fills the gap beside another. A "devices"
  tile in the big-tiles look becomes one card per device (tap = its main action, long press = all of
  it). Everything is drawn from `/api/meta` - no kind of device is hard-coded.
- Live camera video: the app opens the hub's live socket itself (entity.live + ?quality=sub in
  tiles, main full screen) through the mTLS session with its Bearer token and Cloudflare headers,
  and feeds the frames to the shared player page ../mobile-shared/live-player.html (bundled as a
  resource, loaded with loadFileURL in a WKWebView). The still shows until video plays and again
  if it fails (retried after 15 s). At most 4 tiles stream at once; off-screen tiles and a
  backgrounded app stop. Needs MSE/ManagedMediaSource in WebKit (iOS 17.1+); otherwise stills.
- Alert videos: an alert in the camera screen (tapped in Alerts, a Recent alerts tile, the camera's
  list, or its notification) plays its `clip` from `GET /api/clips/<integration>/<clip id>`
  (`AlertClip`, same address as the web's player). It is streamed: a first `Range: bytes=0-1`
  request gives the clip's length, then AVPlayer plays an `AVURLAsset` under a private
  `newman-clip:` scheme whose `ClipStreamLoader` (`AVAssetResourceLoaderDelegate`) turns each of the
  player's reads into a Range request through the mTLS session with the hub's headers (AVPlayer
  can't present the client certificate itself) and passes the bytes on as they arrive - playback
  starts within moments and seeking works (`ClipRange` in Shared/Core, checked on Linux). A clip
  still being recorded has no byte ranges on the hub: that first request then brings the whole mp4,
  which plays from a temporary file. The snapshot shows until the video is ready to play, with
  Retry if the hub has no video yet or loading fails. Tapping a camera notification (`clip`, `frigate_event_id`)
  opens the Alerts tab and that alert's video (looked up in `GET /api/alerts`).
- Sensitive commands (`sensitive: true`) ask for Face ID / Touch ID with passcode fallback first;
  the switch is in Me. Notification buttons marked sensitive use `.authenticationRequired`.
- Notification buttons are posted to the push's own `action_url` (`/api/public/actions`) with
  `{button, subject, deviceId, exp, token}`; a refusal comes back as a local notification with the
  hub's words.
- **People**: everyone `GET /api/people` returns ("Home", a named place or "3.2 km away", "updated
  4 min ago"), each with their **colour** (`color`, "#RRGGBB") and **photo** (`photo`,
  `/api/people/<id>/photo?v=<hash>` or null) - `PersonAvatar` everywhere they appear (rows, People
  tile, map pin, Me): the photo through the mTLS session (`ImageLoader`, cached by its `?v=` URL, so a
  new picture is a new URL), else their single initial on their colour (older hubs without `color`:
  a preset by position). An older hub (404) shows "Location sharing isn't set up on your hub yet".
- **Tap-to-refresh** (no live follow): tapping someone (or the refresh button on their full-screen
  MapKit map) posts `POST /api/people/{id}/refresh` and shows "Updating…" with a spinner until a
  `{"type":"person"}` message (or a reload) brings a newer fix, or 45 s pass ("Couldn't reach their
  phone right now"). The hub allows one per 2 minutes per person: a 429 shows "Updated recently - try
  again in N s" for a few seconds; a 404 (older hub) does nothing. The states are `PersonRefresh` in
  Shared/Core (checked on Linux); the list and map only re-read the hub every 30 s otherwise, which
  never wakes anyone's phone.
- **Me → How your family sees you**: twelve colour swatches (`PersonLook.presets`, plus the current
  one if it's custom) saved with `PUT /api/me/profile {"color"}`; **Photo**: PhotosPicker (no
  permission prompt), cropped to the centred square and scaled to at most 1024 px on the phone, sent as
  JPEG to `PUT /api/me/photo` (the hub makes it 256 px); "Remove photo" is `DELETE /api/me/photo`.
  Shown once the hub lists the account's person (`user.personId`).
- **Share my location** (Me): from `GET /api/me/location` - hidden for "off", a switch for "choice",
  on and locked ("Required by Chris") for "required". Turning it on explains, asks "While Using the
  App", then explains again and asks for "Always" (iOS's one-time "Change to Always Allow" prompt).
- **Reporting and battery** (`LocationSharing`): in the background only significant-location changes
  and visits (no GPS, no blue bar: `showsBackgroundLocationIndicator = false` with "Always"); iOS
  relaunches the app for them after it was closed. Each fix goes to `POST /api/me/location/report`
  (the reply's `live` is ignored; `everySeconds` is kept). The phone never streams: a viewer's tap
  makes the hub send ONE locate - the silent push `{"aps":{"content-available":1},"newman":{"type":"locate"}}`
  or a location push (below) - which takes one fix, posts it with `source: "refresh"` and stops. If
  both arrive for the same tap only the first answers (`LocatePush.claim`, a 30 s mark in the App
  Group). With the hub's 2-minute limit that is at most 30 one-shot fixes an hour, and only while
  someone keeps tapping. Nothing is ever shown to the person being viewed.
- **Location pushes** (`LocationPushExtension`, works with the app swiped away): with sharing on and
  "Always", the app calls `startMonitoringLocationPushes` and sends the token to
  `POST /api/me/location/push-token {deviceId, token, env}` (again after each device registration,
  since the hub only accepts it for a registered iPhone). The hub then wakes the extension with an
  APNs location push for a tap instead of the silent push. The extension checks the App Group flag
  `location.sharing` (kept by the app) and the shared-Keychain sign-in, takes one fix
  (`requestLocation`, nearest 10 m; after 9 s Core Location's newest fix if under 2 minutes old),
  posts it with `source: "refresh"` through `NewmanURLSession` (client certificate, Cloudflare
  headers, Bearer token from the shared Keychain) and completes within 15 s. Sharing off, losing
  "Always" or signing out calls `stopMonitoringLocationPushes` and posts `token: null` so the hub
  forgets it. The rules and bodies are `LocationPushRule` / `LocatePush` in Shared/Core (checked on Linux).
- **Removed devices**: a live `{"type":"entity-removed","id"}` drops that device (`EntityUpdate`);
  new devices arrive as ordinary `{"type":"entity"}` messages.
- **Device wording**: tiles, the device sheet, chips and VoiceOver use the hub's `stateLabel` (the
  kind's notes, e.g. a wall tablet's "Screen off") and only work it out from the kind's states when
  an older hub sends none (`DeviceLogic.stateLabel(entity, kind)`, `stateText`).

### Accessibility (phone)

The phone follows the system settings; the wall keeps its fixed look (it shares `Shared/UI`, so all
of this is switched on only by `Theme.followsAccessibilitySettings = true` in `NewmanApp.init`).

- **Larger Text (Dynamic Type)**: `AppFont.sora` / `manrope` become `Font.custom(_:size:relativeTo:)`,
  each design size scaling like the nearest system text style (`AppFont.textStyle(for:)`), exactly
  the design size at the default setting. Lists, sheets, settings, People, Alerts and the camera
  screen scale fully. Fixed shapes are capped with `cappedDynamicType()`: the placed phone grid
  (square cells) at accessibility 1, the thermostat ring's text at xxxLarge, the account initial
  at xxLarge; single-line labels there shrink to fit (`minimumScaleFactor`) and tiles that overflow
  scroll inside their cell. Sheets and covers opened from a capped view put the person's size back
  (`fullDynamicType()`). SF Symbol icon sizes (`.system(size:)`) stay fixed.
- **VoiceOver**: devices (cards, grid buttons, rows) read as one element: name, the hub's state
  wording, a hint with what a tap does, and a **More** action for the device sheet; the dial and
  sheet controls are adjustable (swipe up / down) with Raise / Lower buttons; Face ID-gated
  commands say so in their hint; tiles, pages, sections and day groups are headers; people rows,
  avatars and map pins say who and where; the camera picture is one image ("Driveway, live video,
  camera 2 of 3", three-finger swipe changes camera, the web player is hidden); chosen chips and
  segments carry the selected trait; toasts and sign-in errors are announced. Widgets: each device
  tile and Lock Screen widget reads name, state and action (no layout change).
- **Reduce Motion**: the toast fades instead of sliding, the people map jumps instead of gliding,
  the connection-security section opens without the ease.
- **Increase Contrast / Reduce Transparency**: glass cards, pill buttons and status chips get an
  opaque base (`Theme.strongCardFill`) and a clear outline (`Theme.strongBorder`).

## Widgets, controls and Siri (phone)

The `NewmanWidgets` extension and `Shared/Widgets` (compiled into the app too). Nothing about a
particular kind of device is built in: what a tile shows, what tapping it does, which commands are
sensitive and which devices can be a switch all come from `/api/meta` kind metadata and the hub's
JSON (`WidgetLogic` in Shared/Core, checked on Linux).

- **Adding them**: long-press the Home Screen → Edit → Add Widget → Newman → **Devices** (small 2×2,
  medium 4×2, large 4×4 tiles), then long-press the widget → Edit Widget → **Devices** (pick and order;
  the list is every device of the hub a widget can show - not cameras or remotes - in sections by room,
  then by type, with search) and **Confirm First**. Lock Screen: customise the Lock Screen → add
  **Device** (circular, rectangular, inline) → pick one device; **Tap Runs Its Action** (on by
  default; off = tap opens that device in the app) and **Confirm First**. Controls (iOS 18): Control
  Center → + → Add a Control → Newman → **Newman Switch** (on/off devices) or **Newman Button** (its
  action right now: open/close the garage, lock/unlock…), then tap it to pick the device; the same
  controls go on the Lock Screen and the Action button (Settings → Action Button → Controls).
- **Tiles**: icon, name, state ("On · 60%", "Locked", "22.5 °C"); a lit tile for an active device,
  the warning colour away from the secure state; "Offline" dimmed. A controllable tile is an App
  Intents button (iOS 17 interactive widgets) that sends the device's tap action - the app's own rule
  (`DeviceLogic.tapAction`: the on/off pair, else the command that isn't the current state) - to
  `POST /api/entities/{id}/command`; a sensor just shows its reading.
- **Sensitive actions need an unlocked phone**: actions the kind metadata (or the hub's widget
  state) marks `sensitive` (unlock, open the garage, disarm…) never run from a locked phone. A tile or
  control button runs them through `RunSensitiveActionIntent` (`authenticationPolicy =
  .requiresAuthentication`: from the Lock Screen / Control Center iOS asks for Face ID / passcode
  first); everything else goes through `RunDeviceActionIntent` (`.alwaysAllowed`, runs while
  locked). Siri's **Unlock** and **Open** intents are `.requiresAuthentication`; Turn on / Turn off /
  Close / Lock are `.alwaysAllowed`, and if the command they resolve to is sensitive (a siren whose
  `turn_on` is marked sensitive, say) they refuse while the phone is locked ("Unlock your iPhone to
  turn on Siren.") - the same for the Control Center switch. "Locked" there is read from a small
  App Group file written with complete file protection (`PhoneLock`), readable only while unlocked.
- **Confirm First** (per widget / control, off by default): widget and control taps only - for a
  sensitive action the intent calls `requestConfirmation` first. Siri and Shortcuts never ask a
  confirmation question: the command just runs (after the unlock above when it's sensitive).
- **Siri / Shortcuts** (`NewmanShortcuts`): "Turn on / Turn off / Open / Close / Lock / Unlock
  <device> with Newman" - the kind's on/off pair, or its command with that id. The app refreshes the
  device names Siri knows after each load (`updateAppShortcutParameters`).
- **Reaching the hub**: like the other extensions - address in use (then the hub's other saved
  address), session token, Cloudflare headers and mTLS certificate from the shared Keychain, so the
  widgets work away from home. Not signed in: "Sign in to Newman".
- **State**: `GET /api/widgets/state?ids=a,b,c` → `{"devices":[{id, name, kind, state, stateLabel,
  available, icon, area, actions:[{command, label, sensitive}], reading:{value, unit}|null}]}` (missing
  fields tolerated; older hubs without it (404): built from `/api/entities` + `/api/meta`). Kinds are
  cached 6 h and the last states kept in the App Group, so an unreachable hub shows the last state dimmed.
- **Freshness**: after an action the intent waits ~1 s and reloads all widgets and controls; the
  timeline asks again 30 s after an action, else every 15 minutes; the app reloads them when it goes
  to the background. **Widget push updates (iOS 26+)**: `NewmanWidgetPushHandler` gets WidgetKit's
  push token, keeps it in the App Group and sends `POST /api/widgets/push-token {token, kind:
  "ios-widget", deviceId, topic: "ca.nimaste.newman.push-type.widgets", apnsEnvironment: "sandbox"
  (debug builds only)}`; the app re-sends it after signing in or when the address or session changes.
  The hub pushes (`apns-push-type: widgets`, topic `ca.nimaste.newman.push-type.widgets`, payload
  `{"aps":{"content-changed":true}}`) when devices change, at most once per 30 s per phone.

## Live status (Live Activities, phone)

While a device is in one of its "live status" states (the device's Live status setting on the
hub), or an automation's **Show live status** runs, the hub shows it on the Lock Screen and in the
Dynamic Island. Same idea as the Android phone's ongoing notification; nothing kind-specific here.

- **Drawing** (`NewmanWidgets/LiveStatusActivity.swift`, iOS 26 extension): title (+ subtitle), the
  time since it started (counted on the phone from `since`, so no timer pushes), up to two device
  rows (icon, name, state, up to two buttons), then "+N more" (the hub's `more` plus rows without
  room). Dynamic Island: the lead device's icon, and its state or "N live" when compact. Tapping
  opens the app on the lead device (`newman://device?id=…`). The buttons run `RunDeviceActionIntent`
  in the extension (the app isn't opened); a `sensitive` one (unlock, open…) is refused until the
  phone is unlocked. The final state (`active: false`) shows without buttons.
- **Types** (must match the hub): `NewmanLiveAttributes` (`Shared/Widgets`, app + extension) with
  attributes `{key, title}`; its ContentState is `LiveStatusState` (`Shared/Core/LiveStatus.swift`,
  checked on Linux): `{title, subtitle, since (seconds since 1970), active, devices:[{id, name, icon,
  stateLabel, active, actions:[{command, label, sensitive}]}], more}`, missing fields tolerated.
- **Tokens** (`Newman/App/LiveActivities.swift`): `POST /api/live/tokens` with `{kind:
  "push-to-start", token, deviceId, apnsEnvironment: "sandbox" (debug builds only)}` (iOS 17.2+, so
  the hub can start one by push) and, for each running activity, `{kind: "update", activityKey, token,
  deviceId}`; `DELETE /api/live/tokens {kind: "update", activityKey, deviceId}` once it has ended or
  been swiped away. Re-sent when the token, the hub address or the session changes.
- **Matching the hub**: on start and back in the foreground the app reads `GET /api/live/current`
  `?deviceId=…` (`{"activities": [{key, title, contentState, attributes}]}`) and ends local ones the hub no longer shows, starts the ones it
  couldn't start by push (no push-to-start token yet) and updates the rest. An unreachable hub
  changes nothing. Signing out ends them all. iOS 26+ only (the extension that draws them is iOS
  26): on an older iPhone the app registers no live status tokens.
- Settings → Newman → **Live Activities** turns them off for the phone (iOS's own switch).
- **After pulling it**: run `xcodegen generate` (new source files; `NSSupportsLiveActivities` is in
  `Newman/Resources/Info.plist`). No new capability: the pushes use the app's existing Push
  Notifications entitlement and the hub's APNs key (topic `ca.nimaste.newman.push-type.liveactivity`).

## The wall iPad

- Keeps the screen on while showing (unless the page calls `NewmanWall.keepAwake(false)`: then the
  iPad's Auto-Lock applies, as on the Android wall), hides the status bar, and **dims** after N minutes
  without a touch (default 5; 0 = never) to a low brightness (default 15%, never below 8% -
  `WallBrightness` in `Shared/Core/WallScreen.swift`, the Android wall's rules; older saved levels are
  raised to that). The hub's page draws the dimmed screen itself (the time softly; Christmas's greeting
  and characters when the top bar had no room; snow paused) - the page's moon button dims too. The first
  touch only wakes it and doesn't reach the page. While awake the wall uses **the iPad's own
  brightness** by default (Control Center), or a level of its own (settings); the iPad's level comes
  back when the wall leaves the front.
- **Shell colour**: the loading and "Can't reach Newman" screens use the page's theme colour
  (`NewmanWall.setTheme`, kept for the next start - `WallShell`; Midnight until a page says).
- **Settings**: hold the **top-left corner for 3 seconds** (or the clock menu's *Tablet settings*), then
  Face ID / Touch ID / passcode: server (the Server screen), certificate, Cloudflare token, dim delay
  (up to 60 min), the iPad's own brightness or a level, dimmed brightness (8-50%), reload, sign the wall out.
- **JavaScript bridge** - `window.NewmanWall` (injected at document start; fires a `newmanwall` event),
  the same as the Android wall's: `native` (true), `version` (string), `platform` ("ios"),
  `setBrightness(0..1)`, `wake()`, `dim()`, `isDimmed()`, `keepAwake(bool)` (default on),
  `openSettings()`, `openServer()` (the sign-in page's Server button), `setTheme({background, scheme})`;
  on dimming and waking the app dispatches a cancelable `newmanwall-dim` event (`detail.dimmed`) -
  `preventDefault()` means the page drew its dimmed screen, otherwise the app darkens it.
  For voice `voiceConfig(json)`, `voiceDone()`, `recordClip(ms, fixed)`, `stopClip()` (see [Voice](#voice-hey-newman)).
  The app answers with `window.NewmanVoice.event({...})`, only while the hub's own page is showing.
- **Battery reports** (`BatteryReporter`, rules in `Shared/Core/TabletBattery.swift`): while the wall
  is active, `POST /api/tablet/battery` at start once the page is signed in, every 5 minutes, and at
  once when charging changes or the level moves 5 points - all with `"screen": "on"`, one at once when
  the wall comes to the front. Going inactive or to the background (the screen locking) sends a last
  report with `"screen": "off"` (best effort, in a short background task). **iPadOS gives an app no
  background schedule**, so nothing more comes until it's woken: the hub shows *Screen off* for up to
  12 hours (battery unknown after 15 minutes, so smart charging leaves it alone), then offline. Sent
  through `NewmanURLSession` (client certificate, Cloudflare headers) with the page's cookies from the
  web view's cookie store, or - no session cookie for the current address - the tablet's token as a
  Bearer (kept from the page's localStorage `newman.tablet.token` when a page loads,
  `BatteryReporter.keepToken`). iOS can't tell the power source or read temperature/voltage: `plugged`
  is "ac" while charging or full, the others `null`. Failures are logged (subsystem
  `ca.nimaste.newman.wall`, category `NewmanBattery`, with the HTTP status) and retried 5 minutes later.

### Voice ("Hey Newman")

The same voice as the Android wall app (`android/README.md` → *Voice*), with the same rules and the
same page events, so `web/src/wall/VoiceLayer.jsx` and `TeachVoice.jsx` work unchanged:

- **Wake word, on the iPad:** openWakeWord's `melspectrogram` + `embedding_model` + the chosen wake
  model (`hey_newman` unless the tablet's *Voice* settings pick another), run with ONNX Runtime (CPU,
  one thread) on every 80 ms of 16 kHz mono audio (`Voice/OnnxWakeModels.swift`). The models come
  from the hub (`GET /api/voice/wakeword` + its three files, with the page's cookies like the
  battery reports) and are kept by version in Application Support/voice (`Voice/WakeModelStore.swift`):
  another wake word, a retrained model or a new sensitivity reaches the tablet within seconds,
  without a new app. The page passes `{enabled, threshold, version}` (`voiceConfig`); the threshold
  already includes the tablet's sensitivity. A wake-up needs two frames in a row at the threshold
  (or one well over it), then 2 s of quiet.
- **After the wake word:** a short chime (made in code, `Voice/Chime.swift`), the screen wakes from
  dim, the page shows *Listening…*, and the app records until 0.8 s of quiet after speech (against
  the room's noise level from just before the wake word, with a zero-crossing check; nobody
  speaking for 5 s = `cancel`, the page's "I didn't catch that"; 8 s at most). The PCM goes to the
  page (`{type: "audio", rate, pcm}`), which posts it to `api/voice/utterance`, ducks its music,
  shows and speaks the reply at the tablet's *reply volume* in its *reply voice*, and does the
  action. The microphone is released until the page calls `voiceDone()` (30 s at most).
- **Audio session** (`Voice/WallMic.swift`): `.playAndRecord`, mode `.measurement`, options
  `.defaultToSpeaker` + `.mixWithOthers` + `.allowBluetoothA2DP`; an `AVAudioEngine` input tap
  converted to 16 kHz mono Int16 with `AVAudioConverter`. `.measurement` = no iOS voice processing,
  the unprocessed microphone the Android wall records (`VOICE_RECOGNITION`) and openWakeWord was
  trained on; the page ducks its own music as soon as the wake word is heard, so the command is
  recorded over a quiet room. `.voiceChat` was ruled out: its echo cancellation would help over
  loud music, but it ducks all other audio, makes playback sound like a phone call and changes the
  microphone level the wake threshold was tuned for. If playback is noticeably quieter on some iPad,
  `WallMic.mode = .default` is the one-line alternative.
- **Only in the foreground:** listening stops when the app goes inactive/background (iOS gives no
  background microphone for this) and starts again when the wall is back in front. The screen stays
  on while the wall shows (`isIdleTimerDisabled`, as before). A call, FaceTime or Siri taking the
  audio (an `AVAudioSession` interruption) lets go until it ends — iOS's counterpart of Android's
  MicGate. A configuration / route change restarts the tap with the new format; a tap that goes
  quiet for 3 s is restarted.
- **Microphone permission:** `NSMicrophoneUsageDescription`; asked once, the first time the page
  turns voice on (or a Teach clip is asked for). Denied = no voice (Settings → Newman Wall →
  Microphone).
- **Teach "Hey Newman"** (`Voice/ClipRecorder.swift`): `recordClip(ms, fixed)` pauses the wake
  word, records one clip (`ClipCapture`: a spoken one from just before the first word to the pause
  after it, 1–6 s; a fixed one 1–35 s) and answers `clip` / `clip-level` / `clip-none` /
  `clip-error`; `stopClip()` hands over a fixed clip early or drops a spoken one. The page uploads
  the clips (`api/voice/teach/clips`), so a household can train from an iPad too.
- The pure parts (framing, levels, ring buffer, noise floor, end of speech, the trigger,
  openWakeWord's buffering, configs, clip cutting, page events, the chime) are in
  `Shared/Core/Voice.swift`, line for line Android's `core/voice`, tested on Linux
  (`Tests/LinuxRunner/VoiceTests.swift`, the same cases as `VoiceTest.kt` / `ClipCaptureTest.kt`).

**Parity with the Android wall app**

| | iPad |
|---|---|
| Wake word models, download by version, threshold/sensitivity, wake word choice | same (`api/voice/wakeword`) |
| Trigger, endpointing, 5 s "didn't catch that", 8 s max, chime skip, 30 s reply wait | same (shared numbers) |
| Page events and bridge (`wake`, `level`, `audio`, `cancel`, `error`, `clip*`; `voiceConfig`, `voiceDone`, `recordClip`, `stopClip`) | same |
| Reply volume, reply voice, music ducking, timers/alarms, "Hey Newman, stop" | same (done by the page) |
| Teach "Hey Newman" clips | same |
| Only in the foreground, mic released during the reply | same |
| Giving the mic to another app (Android: AudioManager recording callback) | iOS: audio-session interruptions (calls, Siri) |
| Coming back after Alexa (`AssistantWatch`) | not applicable (iOS has no other assistant taking over the screen) |
| Device-owner self-grant of the microphone | not applicable (iOS asks once) |
| Built and run on a real iPad | **not yet** — written without a Mac; Linux checks only |

**Most likely to need fixing in Xcode** (none of this could be compiled here):

1. `project.yml` → `packages: onnxruntime` (exact 1.20.0): run `xcodegen generate`, let Xcode
   resolve it. If 1.20.0 isn't a tag of that repo, pick the nearest one ≥ 1.17.
2. `Voice/OnnxWakeModels.swift`: the module name (`OnnxRuntimeBindings` for the Swift package,
   `onnxruntime_objc` for CocoaPods — it tries both) and the Objective-C API's Swift names:
   `ORTEnv(loggingLevel:)`, `ORTSessionOptions.setIntraOpNumThreads` / `setGraphOptimizationLevel`,
   `ORTSession(env:modelPath:sessionOptions:)`, `inputNames()` / `outputNames()`,
   `run(withInputs:outputNames:runOptions:)`, `ORTValue(tensorData:elementType:shape:)`, `tensorData()`.
   Check the log line *Wake-word model … loaded (16 frames)*.
3. `Voice/WallMic.swift`: `AVAudioEngine` tap + `AVAudioConverter` (48 kHz → 16 kHz; the `downmix`
   flag), the `@Sendable` consumer and the tap block made outside the main actor (Swift 6 strict
   concurrency would otherwise trap on the audio thread).
4. **WKWebView and the audio session:** WebKit may set the app's session itself when the page
   plays music or the reply (to `.playback`), which stops the engine. `WallMic` re-applies
   `.playAndRecord` and restarts the tap on the configuration change and after 3 s of silence from
   the tap; check in Console (subsystem `ca.nimaste.newman.wall`, category `NewmanVoice`) that
   "Hey Newman" still works while music plays and right after a reply.
5. `AVAudioApplication.requestRecordPermission` / `recordPermission` (iOS 17 API).
6. Wake scores with `.measurement` (no AGC) may come out lower than on Android: if it needs to be
   shouted, try *Wake sensitivity → high* for the tablet first, then `WallMic.mode = .default`.

**To check on a real iPad:** voice on in Household → Accounts → the tablet → *Voice*; reload the
wall; allow the microphone; “Hey Newman” from across the room → chime + *Listening…* within about
half a second; with music playing; a reply played and “Hey Newman” again straight after; “Hey
Newman, stop” over a ringing timer; Teach “Hey Newman” from the dashboard; the app in the background
(the orange microphone dot goes off) and back; whether “Hey Siri” still works while the wall listens.
### Guided Access (kiosk)

1. Settings → Accessibility → Guided Access → on; set a passcode (and allow Face ID to end it).
2. Same screen: *Display Auto-Lock* → Never.
3. Settings → Display & Brightness → Auto-Lock → Never; consider turning off *Auto-Brightness*
   (Accessibility → Display & Text Size) so the wall's own levels stick.
4. Open **Newman Wall**, triple-click the side/top button → Guided Access → Options: leave Touch on,
   turn off Sleep/Wake and Volume buttons if you like → Start.
5. To leave it, triple-click and enter the passcode.

For a tablet that should come back to the wall by itself after a restart, supervise it with Apple
Configurator and use *Single App Mode* (or an MDM's kiosk mode) instead of Guided Access.
