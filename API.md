# Newman API (v1)

The contract between the hub (`server/`), the web dashboard (`web/`) and the
iOS / Android apps. JSON everywhere; times are Unix seconds (float) unless noted.

Every `/api/*` call needs a signed-in session: the `newman_session` cookie (browser), the `newman_wall`
cookie (the wall page, `kind: "tablet"`) or `Authorization: Bearer <token>` (apps, and the wall page), from
`POST /api/auth/login`. A Bearer token wins; without one the dashboard's `newman_session` wins over
`newman_wall`, so the two sign-ins share a browser without replacing each other (a wall tablet signed in
before `newman_wall` keeps its sign-in in `newman_session`; signing the wall in again moves it).
`kind: "tablet"` takes only an account with role `tablet` (403 "This page is for wall tablet accounts - sign
in with the tablet's own account (add one under Accounts)"), and a `tablet` account only `kind: "tablet"`
(403 "This is a wall tablet's account - sign in with it on the wall tablet"). A tablet sign-in of an account
that isn't a wall tablet's (from before, or its role changed) isn't accepted and is ended.
`POST /api/auth/logout` clears only that sign-in's own cookie. Open without one:
`/health`, `/api/meta`, `/api/hello`, `/api/auth/state`, `/api/auth/login` and `/api/auth/setup` (first run, from
home only). Away from home the admin's sign-in policy applies (client certificate via Caddy mTLS
and/or a 2FA code - see README). `/api/public/*` is authenticated per request by HMAC tokens minted
into push notifications (and webhook secrets).

`GET /api/auth/sessions` (admins: `?all=1` for every account) lists the signed-in devices:
`{"id", "kind": "browser|app|tablet", "deviceName", "user", "createdAt", "lastSeen", "where", "dashboardId", "current",
"live", ...}`. `live` = not expired and seen in the last 72 hours (a device in use checks in every few minutes); a
tablet's old sign-in left behind by a new one stays listed but isn't live. Expired sign-ins are deleted, not listed.

---

## Meta

`GET /api/meta` — everything a client needs to render generic UI.

```json
{
  "version": "v1.2.0",
  "categories": [
    {"id": "security", "name": "Security"}, {"id": "garage", "name": "Garage"},
    {"id": "lighting", "name": "Lighting"}, {"id": "climate", "name": "Climate"},
    {"id": "cleaning", "name": "Cleaning"}, {"id": "other", "name": "Other"}
  ],
  "kinds": [
    {"id": "lock", "name": "Lock", "plural": "Locks", "category": "security",
     "states": [{"id": "LOCKED", "label": "Locked"}, {"id": "UNLOCKED", "label": "Unlocked"}, {"id": "JAMMED", "label": "Jammed"}],
     "activeState": "UNLOCKED",
     "commands": [{"id": "lock", "label": "Lock", "icon": "lock", "confirmsTo": "LOCKED", "sensitive": false},
                  {"id": "unlock", "label": "Unlock", "icon": "unlock", "confirmsTo": "UNLOCKED", "sensitive": true}]},
    {"id": "alarm", "...": "..."}, {"id": "contact", "...": "..."}, {"id": "motion", "...": "..."},
    {"id": "doorbell", "...": "...", "events": [{"id": "pressed", "label": "Pressed"}]}
  ]
}
```

* `version` — the hub's `APP_VERSION`: the release tag the image was built from (`dev` for a local build).
* `activeState` — the state an automation condition means by "is on" (unlocked, open, motion…).
* `sensitive` commands (unlock, disarm, open garage) need Face ID / fingerprint on a phone.
* `automationCommands` — what only an automation's actions can do with one (never a device control):
  lights have `brighten_while_held` / `dim_while_held` (`step`, `min`, `max`: a light stepped rather than
  ramping by itself moves by `step` and never goes below `min`, so dimming never turns a light off).
* An event's `ends` — for a button held down (`button_1_long`, a Pico's `raise_long`), the event that says it
  was let go (`button_1_released`, `raise_released`); a "…while held" action stops on it.
* A remote's `attrs.events` — the events that device really has. A Lutron Pico lists, per button (`on`,
  `favorite`, `off`, `raise`, `lower`), the button, `<button>_long` and `<button>_released`: `raise` once
  it's let go within 0.5 s, `raise_long` once it's been down 0.5 s, `raise_released` when that's let go.

## Connecting an app

`GET /api/hello` — no sign-in. Which hub this is, for an app's "Connect to your Newman" (`Cache-Control: no-store`):

```json
{"app": "newman", "id": "3f9c0a1b2c3d4e5f", "name": "The Garitos", "version": "v1.2.0",
 "outsideUrl": "https://home.example.com/", "localUrls": ["http://10.1.1.2:8093/"],
 "webBuild": "index-Cg0z-opQ.js index-CpiC5FsT.css"}
```

* `webBuild` — the web build the hub serves: the hashed script / style names its `index.html` loads, sorted,
  space-joined (`null` without a build). The live socket's first message carries it too:
  `{"type": "hello", "time", "version", "webBuild"}`. Web pages compare it with what they loaded and reload
  themselves at a quiet moment when it differs.

* `id` — made once, never changes (setting `hubId`). Apps check it before using an address.
* `name` — the `homeName` setting, else `Newman`. `outsideUrl` — the `outsideUrl` setting, else `PUBLIC_URL`, else `null`.
* `localUrls` — `http://<IPv4>:<port>/` for the hub's home-network addresses (not Docker's or VPNs'; `MDNS_INTERFACE` narrows them).

`GET /api/connect-info` (admin) → the same fields plus `link`:
`newman://connect?v=1&id=<id>&name=<name>&local=<first local URL>&outside=<outside URL or empty>` (every value
URL-encoded; no secrets or tokens). The dashboard shows it as a QR code (Household → Accounts → Connect a device).

The name and outside address are admin settings: `GET /api/auth/settings` includes `homeName` and
`outsideUrl`, and `PATCH /api/auth/settings` takes them (`{"homeName": "The Garitos", "outsideUrl": "home.example.com"}`;
`outsideUrl` is normalised to `https://home.example.com/`; plain `http://` only for home-network addresses;
`400 {error}` when it isn't a web address; `"` clears either).

**mDNS**: the hub announces `_newman._tcp.local.` on its HTTP port (instance name = the home name) with TXT
`id=<id>`, `name=<name>`, `v=1`, `ext=<outside URL or empty>`, `path=/`, re-announced when the name or outside
address changes. `NEWMAN_MDNS=0` turns it off.

## Status

`GET /api/status` → `{version, uptime, integrations: [{id, type, name, status, detail}], devices, people, automations, nextRuns: [{automationId, name, at}]}`

`status` is one of `connected | connecting | error | disabled`.

## Entities (devices)

`GET /api/entities[?kind=lock]`

```json
[{"id": "ring.f0b7…", "name": "Side Door", "kind": "lock", "category": "security",
  "integration": "mqtt", "integrationName": "MQTT devices",
  "state": "LOCKED", "stateLabel": "Locked", "available": true,
  "attrs": {"batteryLevel": 100, "tamperStatus": "ok"},
  "health": {"ok": true, "issues": []},
  "room": "Kitchen", "group": null,
  "usedBy": ["auto_1"], "updatedAt": 1790000000.0}]
```

`room` is the room set in Newman, else the connection's `attrs.area`, else null. `group` is the id of the
light/switch group the device belongs to (null when none) - see [Groups and rooms](#groups-and-rooms).

`health.issues` are short strings ("Battery 15%", "Tamper reported", "Offline").

`POST /api/entities/{id}/command` `{"command": "lock"}` → `{"ok": true}` or 409 `{"ok": false, "error": "…"}`.

Live socket (`/api/ws`): `{"type": "entity", "entity": Entity}` whenever a device changes or first appears;
`{"type": "entity-removed", "id"}` when one is gone (its connection removed, or no longer has it — a
connection restarting after its settings are saved only counts once its devices haven't come back within
90 s, or an admin removed it). Clients drop it from their list.
`PATCH /api/entities/{id}` `{"name": "Laundry Door"}` — rename (stored as an override).
Each entity also has `liveStatus` (its "Live status while…" setting; `PUT /api/entities/{id}/live-status`) —
see [Live statuses](#live-statuses).

`POST /api/entities/remove` (admins) `{"ids": [...], "dryRun"?: true, "force"?: true}` — take offline or
unavailable devices (`available: false`, or state `offline`) off the hub for good: each is dropped (live
socket `entity-removed`), and the name and settings given to it in Newman go too ("Video from" links to it
fall back to Automatic). Answers `{"ok": true, "removed": [ids], "devices": [{id, name, usedBy: [{type, id,
name}]}]}`, where `usedBy` lists what still refers to it — `type` `automation`, `dashboard`, `phoneLayout`
(`id` the account, `""` the household default) or `widget`. `dryRun` only reports. Nothing is removed and it
answers 400 `{"error"}` when a device is unknown or online (*"Only offline or unavailable devices can be
removed - Side Door is online"*), or 409 `{"error", "devices"}` when one is still in use and `force` isn't
set. With `force`, those automations, dashboards and layouts keep the missing device (an automation lists
*"An action refers to a device that no longer exists"* and its other parts carry on). If the device's
connection reports it again later, it comes back as a new device, under the connection's own name.

### Pictures, live video and video settings

Every device also carries `still` (`api/entities/{id}/still.jpg?h=`), `alerts` and `live`
(`api/entities/{id}/live?quality=sub|main`, a websocket relaying go2rtc's MSE stream) — each `null` when
it has none — and `video`, its video settings (any device, any connection):

```json
"video": {"from": null, "source": null, "tileStream": null, "fullStream": "front_porch_live",
          "streams": [{"id": "front_porch", "label": "front_porch"}, {"id": "front_porch_live", "label": "front_porch_live"},
                      {"id": "front_porch_sub", "label": "front_porch_sub"}],
          "ownLive": true, "showInCameras": false, "key": "||front_porch_live"}
```

(a Frigate camera); a doorbell from another connection showing that camera's video:
`{"from": "frigate.camera_front_porch", "source": "frigate.camera_front_porch", "streams": [], "ownLive": false,
"showInCameras": true, "key": "frigate.camera_front_porch||front_porch_live", …}`.

What "Video from" offers is in `choices` — per connection, never cameras of its own connection:

```json
"choices": {"own": "Reolink (this device)",
            "options": [{"integration": "frigate", "label": "Frigate", "device": "frigate.camera_doorbell",
                         "cameras": [{"id": "frigate.camera_back_ac", "name": "Back Ac"}, …]}]}
```

`own` — the "its own video" choice (`"<connection> (no video)"` when it has no picture of its own). One
option per other connection with video origins: `device` — its camera at the same network address
(`Integration.video_address`: the host only, never credentials), picked automatically; `null` when there's
none (pick one of `cameras`). `fromIntegration` — the connection of `from`. `derivedFrom` — the device whose
video this one simply is (`Integration.video_derived_from`, e.g. a Frigate zone → its camera): it's never a
source, has no `streams` or `choices` of its own and plays that device's chosen streams.

* `streams` — what its own connection offers (`Integration.live_streams`; `[]` = no choice).
  `tileStream` / `fullStream` — the stream `quality=sub` / `quality=main` plays; `null` = Automatic (the
  connection's own choice — for Frigate `<cam>_sub` for tiles when it exists, else `<cam>`). A picked stream
  the connection no longer offers counts as Automatic (it's kept, and used again if it comes back).
* `from` — "Video from": the device whose live video and pictures it shows (`still`, `live` and wall alerts
  follow it; its own events and `alerts` stay its own). `source` — that device while the link works
  (it exists and has live video of its own), else `null`: then its own picture is used. Links aren't
  followed further. `ownLive` — it has live video of its own (only those, and not derived ones, are sources).
  A link to a derived device means its origin; one that comes back to the device itself means none (it's
  dropped on the next save).
* `showInCameras` — `false` leaves it out of every All Cameras list (its page, tiles, automations and
  alerts are unchanged).
* `key` — changes whenever what its video plays changes; open players reconnect.

`PUT /api/entities/{id}/video` (admins) — any of `{"tileStream", "fullStream", "videoFrom"}` (an id, or
`null` for Automatic / its own; a derived device means its origin) and `"showInCameras"` (`true` / `false`)
→ the updated Entity. 400 with a reason for a stream it doesn't offer, a link to itself, to a device without
live video, or one that would loop. Every screen gets `{"type": "entity"}` for the device and for any device showing its video.


### A device's problems and muting them

Every device's `health` is `{"ok", "issues": [words], "items": [{"id", "text", "muted": null | {"mode",
"until", "by"}}]}`. `ok` / `issues` count only problems nobody muted (what chips, Needs attention and
To do use); `items` lists every current problem. Problem ids: `offline`, `battery_failed`, `battery_low`,
`tamper`, `no_ac`, `not_responding`, `weak_signal`, `ignored_commands`, `jammed`, `overlay_permission`.

* `PUT /api/entities/{id}/mute` `{"issue": id, "for": "day"|"week"|"fixed"|"always"}` → `{"health"}` -
  only a problem the device reports right now (400 otherwise; 404 unknown device). `day` / `week` end by
  themselves; `fixed` goes the moment the problem does; `always` stays until unmuted.
* `DELETE /api/entities/{id}/mute/{issue}` → `{"health"}`.

Any signed-in account may mute, wall tablets included; each change is logged in Activity and pushed live
as an `entity` update. Settings key `mutedIssues` holds them; swapping a device moves them, removing it
drops them.

**Warnings switched off** (admin, the web's Warnings tab): `GET /api/entities/{id}/warnings` →
`{"batteryLow": its own level | null, "houseBatteryLow": Settings level, "hasBattery",
"leftOpenMinutes": its own | null, "houseLeftOpenMinutes": Settings minutes, "hasSecureState",
"warnings": [{"id", "label", "on"}]}` - every problem this device can report. `PUT /api/entities/{id}/warnings`
`{"batteryLow": 5-50 | null, "leftOpenMinutes": 1-240 | null, "warnings": {issue id: bool}}` (any of them;
null = the house setting; 400 for an issue the device can't report, or `leftOpenMinutes` on a device whose
kind has no secure state) → the same. `leftOpenMinutes` is how long a door, window, lock or garage may stay
out of its secure state before Home says it needs you. A switched-off problem never appears in `ok` / `issues` / `items`, Needs attention,
To do or Home; `battery_low` uses the device's own level when set (else Settings > Devices). Settings key
`deviceWarnings`; swapping a device moves them, removing it drops them.

### Groups and rooms

A **group** is one light or switch made of several: a real device with id `group.<slug>`, integration
`groups`, kind `light` when every member is a dimmable light (else `switch`), `attrs.members` (ids),
`attrs.dimmable`. It's `on` while any member is on; `attrs.brightness` is the average of the members that
are on; `available` while any member is. Its commands (`turn_on`, `turn_off`, `toggle`, `set_brightness`,
brighten/dim while held, fades) go to every member together; a command fails only when no member took it.
A device is in at most one group; only lighting devices that turn on and off can be members.

* `GET /api/groups` → `[{"id", "name", "members", "room", "kind"}]`
* `POST /api/groups` (admin) `{"name", "members": [ids], "room"?: str | null}` → the group (400 `{"error"}`)
* `PUT /api/groups/{id}` (admin) any of `{"name", "members", "room"}` → the group
* `DELETE /api/groups/{id}` (admin) → `{"ok": true}` (automations naming it then list the missing device)
* `PUT /api/entities/{id}/room` (admin) `{"room": str | null}` → the entity (null = back to the
  connection's area). A group's room is set with the group.
* `GET /api/rooms` → `["Kitchen", …]` - every room in use, for pickers.

Each change is logged in Activity and sent live as `entity` updates plus `{"type": "home"}`. Settings keys
`groups` and `rooms`; swapping a member moves it, `POST /api/entities/remove` lists a group as `usedBy`
`{"type": "group"}` (and a Home favourite as `{"type": "favourite"}`).

## Dashboards and wall tablets

A dashboard: `{"id", "userId", "isDefault", "name", "theme", "christmas", "pages": [{"id", "name", "icon", "tiles": [Tile]}], "names": {entityId: "Show as"}, "home": Home, "tabNames": "current"}`
on a 12 × 8 grid (`GET /api/dashboards/tiles` lists tile types and their settings; `greeting` and `todo`
are the Today page's tiles). The `people` tile ("Family map", min 3 × 2, default 6 × 5) shows name chips
over a map of the home area (people at one place share a bubble; anyone over ~50 km from Home gets an
edge label instead); its settings: `people` (field type `"people"`: person ids, `[]` = everyone) limits
who it shows, and `mapLook` (`"light"`, the default, or `"dark"`) is the map's look (Apple Maps' colour
scheme, or OpenFreeMap's Positron / Fiord). It reads `GET /api/people` and `GET /api/places`, asks for a
fresh location with `POST /api/people/{id}/refresh` when a person is tapped, and updates from the
socket's `person` messages. `home` is always filled in (defaults shown):

```json
"home": {"page": "overview",
         "morning": {"page": "today",
                     "weekdays": {"from": "06:00", "to": "10:00"},
                     "weekends": {"from": "07:00", "to": "12:00"}},
         "idleMinutes": 2}
```

`page` defaults to the first page; `morning` is `null` when off (or when its page is deleted); a window
must end after it starts (400 otherwise); `idleMinutes` 0 = never go back. The wall tablet decides by
its own clock.

`tabNames` (always filled in): how the wall's page tabs are labelled - `"current"` (the default: every
tab an icon, only the current one named) or `"fit"` (every name when they all fit, otherwise the same as
`"current"`); anything else saves as `"current"`.

`theme` (always filled in): the wall's look - `"midnight"` (the default; dashboards saved as
`"night-glass"` are this), `"glass"` or `"christmas"`; anything else is a 400. `christmas` (always
filled in, defaults shown):

```json
"christmas": {"snow": true, "greeting": "Merry Christmas", "characters": "builtin", "place": "clock",
              "pictures": [], "season": null}
```

`characters`: `"builtin"` | `"pictures"` | `"off"`; `place`: `"clock"` (the top bar, or the dimmed
screen when the bar has no room) | `"background"` | `"off"`; `greeting` up to 40 characters (`""` = none);
`season`: `null` (off) or `{"from": "MM-DD", "to": "MM-DD"}` - every year, inclusive, may wrap over New
Year (`{}` = December 1 to January 6); in it the wall shows Christmas whatever `theme` says. Bad values
are a 400. `pictures` (ids) can't be set by a `PUT` - only by:

`POST /api/dashboards/{id}/pictures` (body: the picture itself, `image/png` or `image/webp`, up to 2 MB;
415 / 413 / 400 otherwise; 409 when it already has 3) → `{"pictures": [id]}`; shrunk to at most 600 px,
kept with its transparency as a PNG under `data/wall-pictures/` (and in backups) ·
`DELETE /api/dashboards/{id}/pictures/{pic}` → `{"pictures": [...]}` ·
`GET /api/dashboards/{id}/pictures/{pic}` → the PNG (the dashboard's owner - the wall tablet - or an
admin; `?token=` works for `<img>`; cached for good, the id is a hash of the picture). Each change sends
`{"type": "dashboard"}`; removing the dashboard removes its pictures.

The `climate` tile ("Climate glance", min 3 × 4, default 5 × 8, `wallOnly`: not offered for phone
layouts) has `entity` (field filter `"temperature-setting"`: a number command in °C / °F), `rooms`
(filter `"temperature"`: a °C / °F reading that is the device's own state, or any temperature a
`climate`-category device reports - not a server's CPU or a disk), `outside` (filter `"forecast"`) and
`page` (field type `"page"`: one of the dashboard's page ids, or none = the thermostat's dial).

`GET /api/dashboards[?all=1]` · `GET /api/dashboards/current` (what this device shows) ·
`POST /api/dashboards` (no `pages` = a starter laid out from the house's devices; `{"copyFrom": id, "name"?,
"userId"?}` = a duplicate of one of yours — an admin: anyone's — with everything on it, its tab names, Christmas
settings and its own copies of the Christmas pictures; a plain create never takes `christmas.pictures`) · `GET|PUT|DELETE /api/dashboards/{id}` ·
`POST /api/dashboards/{id}/default` · `GET /api/dashboards/today-page` → a ready-made Today page
(greeting, To do, agenda, weather) for the editor to add. Each tile type in `GET /api/dashboards/tiles`
also has `phone: [w, h]`, its starting size on a phone layout.

## Phone layouts

*Older phone apps only - current apps use [Phone Home](#phone-home). Kept working for them.*

How the phone apps arrange their pages: a **household default**, and optionally a layout of a
person's own (everyone else uses the default; a reset goes back to it). Wall tablets' accounts have
none (403 / 404).

```
Layout = {"today": bool,                  // the Today card at the top of the first tab
          "pages": [{"id", "name", "icon", "tab": bool,
                     "tiles": [{"id", "type", "x": 0-3, "y": >=0, "w": 1-4, "h": 1-8,
                                "config": {...}, "textSize": number}]}]}   // textSize only when set
```

- **Grid:** 4 columns of square cells (a row is as tall as a column is wide); a page scrolls down.
  Tiles never overlap. Types and settings are the dashboard tiles' (`GET /api/dashboards/tiles`);
  `textSize` mirrors `config.textSize` (either may be sent).
- **Wall-only tiles:** types marked `wallOnly` in the catalogue (Music, Recipes, Climate glance) have
  no `phone` size and are never in a phone layout: a layout saved before they were marked so is sent
  without them (the rows only they covered close up, nothing else moves), and a `PUT` that holds them
  saves without them rather than failing.
- **Tabs:** pages with `tab: true`, in order — at least 1, at most 3; People and Alerts are the
  phone's last two tabs. Other pages open from links at the bottom of the first tab (a dial tile also
  opens the non-tab page holding a dial for the same device, as before).
- **Limits** (400 with `{"error", "errors"}`): 8 pages, 48 tiles a page, `y + h` ≤ 400, unknown tile
  types, tiles off the 4 columns or overlapping, more than 3 tabs, no tab.
- **Live:** when someone's effective layout changes (theirs is saved or reset, or the default changes
  while they use it), their open apps and pages get `{"type": "dashboard", "userId", "phoneLayout": true}`
  on the socket; apps reload `GET /api/phone-layout`. Nobody else is sent anything.

The first household default (made the first time it's needed, then kept) is laid out from the
first admin's default wall dashboard by the rules the apps used before: a Today page (only greeting /
To do / today / calendar / weather tiles) becomes `today: true`; the next 3 pages are tabs, up to 5
more are links; tiles flow in dashboard order, the greeting and wall-only tiles dropped; half-width tiles pair up. Sizes:

| Wall tile | Phone (w × h) |
|---|---|
| dial — wall ≥ 5 × 5 / smaller | 4 × 4 / 2 × 2 |
| weather — wall ≥ 6 wide or "next hours" / else | 4 × 3 (one-line look: 4 × 1) / 2 × 2 |
| devices, big-tiles look | one 1-row card per device (a devices tile of that one device): 4 × 1 when the wall tile is ≥ 6 wide, else 2 × 1 |
| devices list, readings | 4 × (1 + ⌈0.6 × count⌉), 2–6 rows |
| camera, alerts, people | 4 × 3 |
| calendar, today, todo | 4 × 4 |
| attention, house mode | 4 × 2 |

`GET /api/phone-layout` (any signed-in account but a wall tablet) → `{"source": "default"|"own", "layout": Layout}`
— what this phone shows.

`GET /api/phone-layouts/me` · `PUT /api/phone-layouts/me` (a Layout, or `{"layout": Layout}`; it
becomes the person's own) · `POST /api/phone-layouts/me/reset` (back to the default) → `{"userId",
"name", "source", "layout", "updatedAt"}`. Family and guests too, for their own only.

Admins: `GET /api/phone-layouts` → `{"default": Layout, "people": [{"userId", "name", "role", "source",
"updatedAt"}]}` (accounts that aren't wall tablets or disabled) · `GET|PUT /api/phone-layouts/default`
→ `{"layout", "updatedAt", "using": count of people on it}` · `GET|PUT /api/phone-layouts/{userId}`,
`POST /api/phone-layouts/{userId}/reset` (anyone's, same reply as `/me`) · `POST
/api/phone-layouts/reset-all` → `{"ok": true, "reset": n}`. Swapping devices (`POST
/api/entities/replace`) moves phone layouts too (`phoneLayouts` in its counts).

`POST /api/tablet/battery` — a wall tablet's own battery, from its signed-in session (cookie or Bearer;
any role, wall tablets included):

```json
{"level": 54, "charging": true, "plugged": "ac", "temperatureC": 31.5, "voltageMv": 4012,
 "screen": "off", "background": true, "nextReportIn": 900, "dozeExempt": false, "canComeBack": true,
 "appVersion": "1.4.2"}
```

`level` 0–100, `charging` bool, `plugged` `ac|usb|wireless|none`, `temperatureC` / `voltageMv` number or
null; optional `screen` `on|off` (default `on`) and `background` bool (default false: the tablet keeps
reporting with its screen off - the Android wall does while its check-in alarm is armed, every 15 minutes
even in Doze; an iPad can't), `nextReportIn` (seconds until its next check-in at the latest, 0–86400, or
null), `dozeExempt` bool (Android lets it ignore battery optimisation), `canComeBack` bool (the wall may
bring itself to the front with its screen on: *Display over other apps* or device owner; false shows on
the device's `health.issues`) and `appVersion` (text, up to 32 characters). → 204; 400 for a
bad body; 401 signed out. The account becomes one device `tablets.<account id>` (kind `tablet`: state
`charging` / `battery`; attrs `batteryLevel`, `charging`, `plugged`, `temperature` °C, `voltage` V,
`screen`, `backgroundReports`, `batteryStale`, `nextReportIn`, `nextReportAt` (epoch s), `dozeExempt`,
`deepSleep`, `longSilent`, `canComeBack`, `appVersion`). While `screen` is `off` its `stateLabel` is "Screen off"
(the kind's `notes` in `/api/meta`: `[{"attr": "screen", "value": "off", "label": "Screen off"}]` - shown
instead of the state's label while an attr has that value; the state itself stays). Offline after 15
minutes without a report with the screen on; after a screen-off report with `background`: when it was
charging / plugged in or `dozeExempt` (its check-ins should get through) 35 minutes (or `nextReportIn` +
20 minutes when that's later, up to 12 hours), otherwise - on battery, not exempt, so Doze may hold its
check-ins back - 8 hours, with `deepSleep` true once it's past that 35 minutes / `nextReportIn` + 20
(asleep, expected - not offline); 12 hours after one without `background`; `batteryStale` is true once a
screen-off tablet without background reports has been quiet 15 minutes (its level is unknown). Going
quiet (offline, `deepSleep` or `batteryStale`) has smart charging turn its charger on, silently;
`longSilent` is true once it has been quiet 3 hours (12 hours, i.e. offline, for one without background
reports) - then the admins are told once if its charger is on or none is set. Any report sets
`deepSleep` and `longSilent` false again. While such a tablet is
asleep (screen off, background, not overdue) its music player stays available with `screen` `off`
("Screen off") instead of going offline when its page stops reporting.

`GET /api/charging/{account id}` (admin) — a wall tablet account's **smart charging**; 404 for an
account that isn't a wall tablet's:

```json
{"enabled": true, "startBelow": 20, "stopAbove": 80, "charger": "matter.86795ff2c4eb54d6_1_onoff",
 "learned": true, "learnedAt": 1791460800.0, "chargerName": "Smart Plug - Newman Kitchen",
 "device": "tablets.u_ab12", "criticalLevel": 10,
 "choices": [{"id": "matter.86795ff2c4eb54d6_1_onoff", "name": "Smart Plug - Newman Kitchen",
              "group": "Switches & plugs", "available": true}]}
```

`charger` is a device id or null; `learned` / `learnedAt` (epoch s) say the hub learned it (false / null
when picked by hand); `device` is the tablet's battery device (null until it reports); `choices` are the
devices that can be the charger — any whose kind has plain `turn_on` / `turn_off` commands (kinds.py),
grouped by the kind's plural name. `PATCH /api/charging/{account id}` (admin) with any of `enabled`
(bool), `startBelow` / `stopAbove` (whole %, 5–95, start lower than stop), `charger` (such a device id,
or null) → the same object; 400 with the problems. Kept in the settings as `smartCharging`
(`{account id: {...}}`). The hub acts only when the battery crosses a level (and once when turned on or
after a restart): below `startBelow` → `turn_on` the charger, above `stopAbove` → `turn_off`, by
"Smart charging" (activity kind `automation`, `entityId` the tablet). It learns the charger when exactly
one such device switches and, within 10 s, exactly one tablet's `charging` / `plugged` changes the
same way; it never switches anything to find out. Below `criticalLevel` and not charging, or a charger
not responding for 90 s when it should turn on: one push to every admin (kind `followup`, title
"Smart charging"), at most hourly per tablet and again once it has charged.

`chosenBy` is `"user"` (picked by hand, or the answer below), `"learned"` or null. A charger picked by
someone is `learned: true` once the learning sees it work ("Confirmed: charges from …").

**The wrong plug.** When the hub has turned the charger on and the tablet, still reporting, hasn't started
charging within 60 s, it asks once per attempt: `question` in the object above,
`{"id", "text": "Kitchen tablet didn't start charging from Hall plug. Which plug is it on?", "at", "charger",
"choices": [{"id", "name", "icon"}]}` (null otherwise; at most 3 other switchable devices that respond — the
tablet's `area` first, then ones that report `power`, then ones that are off), plus a push to every admin with
those as buttons (`charger:<device id>` and `charger_none` "None of these"; subject `charging:<account id>:<question
id>`, pressed through `POST /api/public/actions`; their category is in `GET /api/push/categories` while the question
is open). Nothing else is switched to find out. `POST /api/charging/{account id}/answer` (admin)
`{"question": id, "plug": one of its choices' ids | null}` → the settings object: the plug becomes the charger
(`learned` false, `chosenBy` "user") and is turned on; null leaves the charger as it is. 409 when that question
is no longer open or the plug isn't one of its choices. A new attempt (the next time it's turned on) may ask again.

## Phone widgets and quick tiles

For the phones' home-screen widgets (iOS WidgetKit, Android Glance) and Android's Quick Settings tiles
(widgets.py). Any signed-in account; what a device is and does comes from kinds.py alone.

A device is **widget-showable** when its kind has a one-tap command (no value needed), reports a reading
(a kind whose state is a number, or one of its `summary` properties is a number), or has more than one
state — cameras and camera zones excepted (a picture, not a state). Remotes, webhooks and the like aren't.

`GET /api/widgets/devices` — every widget-showable device, for the pickers, sorted by room (none last),
then name:

```json
{"devices": [{"id": "lutron.device_5", "name": "Porch", "kind": "light", "kindName": "Lights",
              "area": "Outside", "icon": "light", "controllable": true}]}
```

`icon` is the device's own `attrs.icon` when it has one, else its kind id (apps map kinds to icons).
`controllable` = it has one-tap commands. `kindName` (the kind's plural) is for grouping by type.

`GET /api/widgets/state?ids=a,b,c` — those devices now, in that order (at most 64; unknown or no longer
showable ones are left out; without `ids`, every showable device):

```json
{"devices": [{"id": "ring.f0b7…", "name": "Side Door", "kind": "lock", "state": "LOCKED",
              "stateLabel": "Locked", "available": true, "active": false, "tone": "good", "icon": "lock",
              "area": "Mudroom",
              "actions": [{"command": "unlock", "label": "Unlock", "sensitive": true, "primary": true},
                          {"command": "lock", "label": "Lock", "sensitive": false, "primary": false}],
              "reading": null}]}
```

- `actions` — the kind's one-tap commands. What a tap does **now** comes first with `"primary": true`
  (none is primary when a tap has nothing sensible to do): the on/off pair toggles when the kind has
  exactly two plain, non-sensitive commands and one makes it active; otherwise the first command whose
  `confirmsTo` isn't the current state; a single command (a button's Press) always. The same rule as the
  apps' own tiles (`HubModel.tapAction`, web `model.js`). `sensitive` is the command's own flag (unlock,
  open, disarm…) — apps may ask before running one.
- `reading` — `{"value", "unit"}` exactly as reported (never rounded): the state itself for kinds whose
  state is a number (temperature, sensor), else the first of the kind's `summary` properties that is a
  number (a thermostat's current temperature, a server's CPU); `null` otherwise.
- `active` — in the kind's `activeState` (on, unlocked, open…); apps highlight it.
- `tone` — the colour widgets give the state, from the kind metadata: `"good"` at the kind's
  `secureState` (locked, closed, dry), `"alert"` an `urgent` kind away from it (a leak), `"active"` away
  from it (unlocked, open) or — for kinds without a secure state — in the `activeState` (on),
  `"offline"`, else `""` (neutral; also between states, e.g. locking).

`GET /api/widgets/home` — the phones' **Home status** widget: every device whose kind has a
`secureState`, and the house modes (`isMode`, an alarm), summed up:

```json
{"secure": false, "headline": "Side Door unlocked", "detail": "garage door closed",
 "problems": [{"id": "ring.f0b7…", "name": "Side Door", "kind": "lock", "icon": "lock", "state": "UNLOCKED",
               "stateLabel": "Unlocked", "urgent": false,
               "fix": {"command": "lock", "label": "Lock", "sensitive": false}}],
 "groups": [{"kind": "lock", "label": "Lock", "text": "Unlocked", "secure": false, "icon": "lock"},
            {"kind": "garage", "label": "Garage door", "text": "Closed", "secure": true, "icon": "garage"},
            {"kind": "alarm", "label": "Alarm", "text": "Disarmed", "secure": null, "icon": "alarm"}],
 "ids": ["ring.f0b7…", "myq.garage", "ring.alarm"]}
```

- `secure` — `true` when every online one is at its secure state, `false` when one isn't, `null` when
  the house has none. Offline devices and ones between states (locking) don't count against it.
- `headline` — "All secure" / "Side Door unlocked" / "2 open" / "2 not secure" / "Nothing to keep secure";
  `detail` — when secure, what's covered ("2 locks · garage door closed · 1 offline"); with one problem,
  the kinds that are fine; with several, each one ("Side Door unlocked · Garage open").
- `problems` — what isn't secure, urgent kinds first, then those with a fix, then by name; `fix` is the
  kind's one-tap command whose `confirmsTo` is the secure state (Lock, Close), `null` when there's none
  (a door sensor, a leak). Run it with `POST /api/entities/{id}/command`.
- `groups` — one line per kind (label: the kind's name, or plural for several), not-secure ones first,
  house modes last; `text` "All locked" / "Locked" / "Side Door unlocked" / "2 open" (+ " · 1 offline"),
  a mode's state label.
- `ids` — every device it covers; asking counts as the account's widgets showing them (reload pushes).

Run an action with the usual `POST /api/entities/{id}/command` `{"command": "unlock"}` (wall tablets'
PIN rule for sensitive commands applies as everywhere; phones aren't asked).

`POST /api/widgets/push-token` `{"token": "<hex>"|"<fcm token>"|null, "kind": "ios-widget"|"android",
"deviceId": "…", "apnsEnvironment"?: "sandbox"|"production"}` → `{"ok": true, "kept": true|false}`; 400
for a bad kind / missing deviceId / a non-hex iOS token. One token per phone and kind (re-registering
replaces it; a token that moved to another install leaves the old one); a null or empty token forgets
it. The APNs environment defaults to the one the phone registered with in `/api/devices/register`.
Kept with the sign-in it came from: signing out, revoking that device or removing the account drops it
(and a token whose sign-in has expired is never pushed to). `DELETE /api/phones/{deviceId}` drops it too.

**Reload pushes.** When a widget-showable device changes in a way a widget shows (name, state, state
label, availability, `active`, `tone`, actions or reading — not a timestamp or an unshown attribute), each live
token gets a reload push, **at most one per 30 s per token**: a change inside that window goes out when
it ends, folded into one. Once an account's widgets have asked for particular devices
(`GET /api/widgets/state?ids=` and `GET /api/widgets/home`, remembered for 3 days), only changes to those count for that account's
phones; until then any showable device's change does (e.g. after a hub restart).
- iOS (`ios-widget`): APNs `apns-push-type: widgets`, topic `<APNS_BUNDLE_ID>.push-type.widgets`,
  priority 5, payload `{"aps": {"content-changed": true}}` — WidgetKit reloads the widgets. Skipped when
  APNs isn't set up. A dead token (410 / BadDeviceToken) is dropped.
- Android (`android`): an FCM data message `{"type": "widgets"}` — skipped when FCM isn't set up (the
  Android widgets also refresh on a tap, from the app's live connection, and every 15 minutes).

## Live statuses

A live status shows devices on phones while they matter — an iPhone **Live Activity** (Lock Screen,
Dynamic Island), an **ongoing notification** on Android: "Garage Door · Open · 12 min" with a Close button.
Any device can have one; what it shows (labels, icons, buttons) comes from its kind (kinds.py), never
from a list of kinds. Two things start one:

- the device's own **"Live status while…"** setting: while it's in one of the chosen states it shows to
  the chosen people; when it leaves them (moments in between — a door moving — don't count) it ends;
- an automation's **"Show a live status"** (see [Automations](#automations) → Live status).

**A device's setting.** Every entity carries `"liveStatus"`: `{"enabled", "states": [state ids],
"recipients": null | [person ids], "choices": [{"id", "label"}], "showing": bool}` — `null` when its kind
has fewer than two states to pick (a reading). `choices` are the kind's states without its in-between
ones; the default `states` is the kind's `activeState` (else every state but its `secureState`).
`PUT /api/entities/{id}/live-status` (admins) `{"enabled"?, "states"?, "recipients"?}` → the entity; 400 for
a state the kind doesn't have, no state, every state (it would never end), an empty recipients list or
someone not in the household. `recipients: null` = everyone. The screens hear the change as an `entity`
message.

**What each person sees.** Their live statuses, newest first; **at most 5**: with more, the newest 4 stay
their own and the rest share one **"N more"** status (slot `more`) listing them, updated as they change.
Each entry: `{"key", "slot", "title", "contentState"}` — `slot` is the live status (`device:<id>`,
`automation:<id>`, `more`), `key` the activity's own id: `<slot>.<start epoch>` plus `.<n>` once it has
been restarted (iPhones end an activity after 8 hours: one still needed after **7.5 hours** is ended and
started afresh, same `since`).

`contentState` (also the iPhone activity's content-state; kept under 3.5 KB):

```json
{"title": "Garage Door", "subtitle": "Open", "since": 1791546327, "active": true, "more": 0,
 "devices": [{"id": "ring.garage", "name": "Garage Door", "icon": "garage", "stateLabel": "Open", "active": true,
              "actions": [{"command": "close", "label": "Close", "sensitive": false}]}]}
```

- `since` — when it started (epoch seconds); apps count the time themselves (the hub never pushes just
  for the clock).
- `devices[].active` — in the state it's shown for; `icon` — the device's own icon, else its kind;
  `actions` — up to 2 one-tap commands that would change it from here (what a tap does first), each run
  with `POST /api/entities/{id}/command` `{"command"}`; `sensitive` ones (open, unlock, disarm) must be
  confirmed on the phone (Face ID / fingerprint / screen lock) first.
- `more` — how many beyond the `devices` listed: **`devices.length + more`** is everything it's about
  (rows that don't fit the size limit are counted here; an "N more" status lists up to 6).
- An **ended** one's final state: `active: false`, no actions, its devices as they are now; really over
  and about one device, the title says it: "Garage Door closed".

`GET /api/live/current[?deviceId=]` → `{"attributesType": "NewmanLiveAttributes", "max": 5, "activities":
[entry + "attributes": {"key", "title"}]}` — the signed-in person's (or, with `deviceId`, that phone's
person's) live statuses now. Apps call it when they open: an iPhone starts any it isn't showing (and ends
ones not listed), Android posts / cancels its notifications.

**Live socket.** `{"type": "live", "activities": [entries], "ended": [entries + "reason": "ended" |
"moved", "dismissAt": epoch]}` to the person's open sockets whenever their list changes (the whole list;
also on connect when there's anything). `moved` = it goes on elsewhere (into / out of "N more", or
restarted): take it away now; `ended` = really over: show the final state until `dismissAt` (2 minutes).

**Android without a live socket.** When FCM is set up, each Android phone of the person gets one data
message per change: `{"type": "live", "event": "start" | "update" | "end", "key", "slot", "title",
"state": "<contentState JSON>", "dismissAt"?: "<epoch>"}` (an `end` whose `dismissAt` has passed: remove it
now). Test phones get the same in `/api/test/outbox` (`"kind": "live"`).

### iPhone: ActivityKit tokens and pushes

The activity type is **`NewmanLiveAttributes`**: attributes `{"key": String, "title": String}`,
content-state as above.

`POST /api/live/tokens` (any signed-in account, not wall tablets):
- `{"kind": "push-to-start", "token": "<hex>", "deviceId": "…", "apnsEnvironment"?: "sandbox"|"production"}` —
  the phone's push-to-start token (iOS 17.2+, `Activity<NewmanLiveAttributes>.pushToStartTokenUpdates`).
- `{"kind": "update", "activityKey": "<attributes.key>", "token": "<hex>", "deviceId": "…", "apnsEnvironment"?}` —
  each running activity's push token (`activity.pushTokenUpdates`), whether the hub started it or the app did.
→ `{"ok": true}`; 400 for another kind, a missing deviceId / activityKey, a non-hex token. One row per phone,
kind and activity; the same token elsewhere is dropped. The APNs environment defaults to the one the phone
registered with in `/api/devices/register`. Tokens are tied to the sign-in they came with (like widget push
tokens): signing out, revoking the device or removing the account drops them; `DELETE /api/phones/{deviceId}`
too. `DELETE /api/live/tokens` `{"kind", "deviceId", "activityKey"?}` (or as query parameters) forgets one —
send it when an activity ends on the phone (dismissed there: the hub doesn't start it again; it starts a new one
only for a new key). An activity token for a key the hub doesn't want (any more) is answered with an `end` at once.

Pushes: `apns-push-type: liveactivity`, topic `<APNS_BUNDLE_ID>.push-type.liveactivity`, priority 10
(state changes only; nothing is pushed just because time passes):
- **start** → the push-to-start token: `{"aps": {"timestamp": <now>, "event": "start", "attributes-type":
  "NewmanLiveAttributes", "attributes": {"key", "title"}, "content-state": {…}, "alert": {"title", "body"}}}`.
  No push-to-start token yet (the app never opened): nothing is sent; the app starts it from
  `GET /api/live/current` when it opens.
- **update** → the activity's token: `{"aps": {"timestamp", "event": "update", "content-state": {…}}}`.
- **end** → the activity's token: `{"aps": {"timestamp", "event": "end", "content-state": <final>,
  "dismissal-date": <now + 120>}}` — `dismissal-date` = now for one that only moved (or was restarted).
Ends go before starts, so a phone never holds more than 5. A dead token (410 / BadDeviceToken) is dropped;
another failure waits 5 minutes before that push is tried again. What each phone was last sent is kept, so
a hub restart neither starts an activity twice nor forgets to end one (after a restart it waits 20 s for
devices to report in first).

## Music (wall tablets)

With the Navidrome connection, each wall tablet account gets one device `<connection id>.<account id>`
(kind `music_player`: state `playing` / `paused` / `idle`; attrs `volume`, `title`, `artist`, `album`,
`cover`, `position` / `duration` (s), `queueLength`, `screen` `on|off`). It's available while the wall page
reports (at least every minute while open); once the page goes quiet it's offline after 3 minutes - unless
its tablet is asleep (screen off, still checking in its battery, see `/api/tablet/battery`): then it stays
available with `screen` `off` (`stateLabel` "Screen off", the kind's `notes`), and anything but `pause`,
`stop` and `set_volume` is refused with “That wall tablet's screen is off right now”. Its commands go through `POST
/api/entities/{id}/command` like any device's: `play`, `pause`, `stop`, `next`, `previous`, `set_volume`
(0–100), `volume_up`, `volume_down`, `play_query` and `shuffle` (value: words — “Jump by Van Halen”, an
artist, “the album 1984”, “my Dinner playlist”; 409 “I couldn't find …”). The hub keeps the queue and
pushes `{"type": "music", "player": Player, "seek"?: seconds}` to **that account's** sockets only.

`Player` = `{"entityId", "state", "volume", "position", "index", "queueLength", "seq", "track": null |
{"id", "title", "artist", "album", "duration", "coverArt", "stream": "api/music/stream/<id>", "cover":
"api/music/cover/<id>" | null}, "upNext": [{"title", "artist"}]}` (`seq` grows with every change — a
client ignores an older one).

An automation action with `"for"` on a player (“play … for 5 minutes”) snapshots it first (queue, song,
position, state, volume) and, when the time is up, restores all of it and pushes `{"type": "music", "player",
"seek": <that position>}` — unless the music was changed meanwhile (other music put on, paused or stopped
by someone), when it is left alone. Either way the run's `log` gets the line (“Put back: Van Halen – Panama
at 1:23, playing (volume 40)” / “Left the music alone: it was changed during the 5 minutes”). The pending
put-back survives a restart. (Integrations: `snapshot` / `marker` / `restore` on the Integration contract;
devices without them get the generic put-back by state.)

- `GET /api/music` → `{"available", "status", "detail", "player"}`; from a wall tablet session it makes
  that tablet's player the first time and counts as a report. `available: false` without the connection.
- `POST /api/music/report` (wall tablet) `{"state"?: "playing|paused|idle|ended|error", "position"?, "trackId"?,
  "byUser"?}` → Player. `ended` / `error` for the current track moves to the next one (the end of the queue
  → `idle`); an empty body = “still here” (the page sends one every minute; offline after 3 minutes).
- `POST /api/music/play` (wall tablet) `{"albumId" | "playlistId" | "songId" | "artistId", "shuffle"?}` or
  `{"query", "shuffle"?}` → `{"ok", "say", "player"}`. A song plays and then the rest of its album; an artist
  plays shuffled. An album or playlist can start part way: `"startSongId"` (or `"index"`, from 0) — the whole
  of it is queued, playing from that song. 400 bad ids, 404 nothing found / not on it, 502 Navidrome didn't answer.
- `GET /api/music/browse` → `{"albums": [Album] (recently played, else newest), "playlists": [Playlist]}`.
- `GET /api/music/albums?offset=` → `{"albums": [Album] (A–Z, 60 at a time), "more"}`.
- `GET /api/music/albums/{id}` → `{"album": Album + {"duration" (s, all songs), "artistId"}, "songs": [Song +
  {"track", "disc"}]}` in album order (404 unknown) — the picker's album page.
- `GET /api/music/playlists/{id}` → `{"playlist": Playlist + {"duration"}, "songs": [Song + {"track", "disc"}]}`
  (404 unknown).
- `GET /api/music/artists` → `{"index": [{"letter", "artists": [{"id", "name", "albums", "cover"}]}]}` — every
  artist, grouped by letter as Navidrome's `getArtists` groups them (its ignored articles, `#` for the rest);
  kept on the hub for 10 minutes.
- `GET /api/music/artists/{id}` → `{"artist": {"id", "name", "albums", "cover"}, "albums": [Album]}` (404
  unknown). Shuffle all their songs with `POST /api/music/play {"artistId"}`.
- `GET /api/music/search?q=` → `{"artists": [{"id", "name", "albums", "cover"}], "albums": [Album], "songs":
  [Song], "playlists": [Playlist]}` (up to 6 artists, 12 albums, 20 songs, 6 playlists; `&full=1` — the
  keyboard's Search key — up to 30, 50, 100 and 60).
  `Album` = `{"id", "name", "artist", "year", "songs", "cover"}`, `Playlist` = `{"id", "name", "songs", "cover"}`,
  `Song` = `{"id", "title", "artist", "album", "duration", "cover"}`.
- `GET /api/music/lyrics/{id}` → `{"source": "navidrome" | "lrclib" | null, "synced": bool, "lines": [{"t":
  seconds, "text"}], "text": "plain text"}` — Navidrome's (OpenSubsonic `getLyricsBySongId`, then `getLyrics`),
  else LRCLIB's when the connection's `lrclib` setting is on (default; only artist, title, album and length are
  sent). `synced` → `lines` sorted by time (LRC parsed on the hub; a blank `text` is a pause); otherwise `text`.
  `source: null` = none found. Kept per song (found: 30 days; none found: 6 hours).
- `GET /api/music/stream/{id}` — the song's audio as Navidrome sends it (type, length, `Range` → 206); the hub
  adds the token, which never reaches the client. `GET /api/music/cover/{id}?size=` — its cover (image).
  Both also accept `?token=` (the wall tablet's), for an `<audio>` / `<img>` without the cookie.

All but `stream` / `cover` answer 503 while Navidrome isn't connected and 404 without the connection.
Voice understands music requests on a tablet that has a player (see Voice).

## Recipes and cook mode (wall tablets)

With the Forkast connection (read only; it keeps only Forkast's sign-in token) the hub keeps the recipe
list and one device `<connection id>.recipes` (kind `recipe_book`: state `ready`; attr `recipes` = how
many). Each account (a wall tablet) has its own **cook mode** on the hub — the open recipe, its step and
servings — pushed as `{"type": "cook", "cook": Cook | null}` to **that account's** sockets whenever voice
or a touch changes it; it closes itself after 3 hours untouched.

`Cook` = `{"recipe": {"id", "title", "image": "api/recipes/<id>/image" | null, "baseServings" | null,
"prepMinutes", "cookMinutes", "totalMinutes"}, "servings", "servingsText" ("1½"), "scaled": bool, "factor",
"ingredients": [{"amount": "1½ cups" | "" (none given), "name", "notes"}], "steps": ["…"], "step" (0-based,
the current one), "read": bool (a step was read or picked), "closesIn": seconds}` — amounts already scaled
and formatted by the hub (exact amounts everywhere: a fraction glyph only when the value is exactly ½, ⅓ etc., otherwise the exact decimal,
normalised unit names).

- `GET /api/recipes?q=&refresh=1` → `{"available" (connected), "status", "detail", "recipes": [{"id", "title",
  "image", "totalMinutes", "steps", "servings"}]}` (A–Z; `q` = words the title contains; `refresh=1` reads the
  list from Forkast first). `available: false, status: null` without the connection.
- `GET /api/recipes/{id}/image` — the recipe's picture through the hub (also `?token=`); 404 without one.
- `GET /api/cook` → `{"cook": Cook | null}` for the signed-in account.
- `POST /api/cook` `{"recipeId"}` → `{"cook"}` — open it (replaces any open recipe). 404 not in Forkast,
  503 not connected / needs signing in again.
- `PATCH /api/cook` `{"step"?: 0-based, "servings"?: number > 0}` → `{"cook"}`; 404 when nothing is open.
- `DELETE /api/cook` → `{"cook": null}`.

A wall tablet's session may use all of these (POST / PATCH / DELETE `api/cook` count as using the house).
Voice works cook mode on the tablet that heard it (see Voice: `action.type` `cook`).

## Phone Home

`GET /api/home` (any signed-in account but a wall tablet) - everything the phone's Home screen shows, all
text worded by the hub:

```
{"cards": [{"id": category id | "today" | "people", "on", "new", "title", "category": id | null,
            "screen": "cameras"|"climate"|"lights"|"security"|"today"|"people"|null,
            "value", "detail", "tone": "normal"|"good"|"warm"|"danger", "camera"?: id}],
 "favourites": [ids, ≤4],
 "status": {"level": "ok"|"warn"|"danger", "summary": "Locked up · doors closed",
            "danger": [{"id", "title", "detail", "entity", "action": {"command", "label"} | null}],
            "warnings": [{"entity", "issue", "text": "Motion Detector · Battery 15%"}]},
 "cameras": {"mode": "latest"|"featured", "featured", "top": {"camera", "label", "zone", "at",
             "snapshot", "clip"} | null},
 "climate": {"thermostat", "rooms": [{"entity", "name", "value"}], "range", "bands"},
 "canAutomate": bool, "canEditHome": bool, "layout": "household"|"own"}
```

- **One card per category with a device in it** (`GET /api/categories`: built-in and made-in-Newman ones,
  after any device moves), plus Today and People. `screen` is the category's purpose-built screen (from the
  category's metadata), `null` = the plain list of that category's devices. A category with no screen gets
  a plain card: one device - its name and state; several - "<n> <kind plural | devices>" and "<n> need a
  look" / "<n> <active state>" / "All quiet". Card id `lights` from older layouts reads as `lighting`.
- **Order and on/off** come from the person's own layout (when they may edit it and have one), else the
  household layout (`layout` says which), else the defaults (Cameras, Climate, Lighting, Security, Today,
  People on; the rest off). A card the layout doesn't mention goes last and off; `new` is true when Newman
  never showed that card before (a category that turned up since) - until a saved layout includes it.
- **Danger:** an alarm that's triggered, a device of an urgent kind out of its secure state (a leak), or
  any other device out of its secure state (a door or garage open, a lock unlocked) for its own
  `leftOpenMinutes`, else Settings > Devices `leftOpenMinutes` (15 to start), or more - with the command that fixes it when the kind has one. **Warnings:** device problems nobody
  muted or switched off.
- **Lights** counts groups and the lighting devices with a brightness that aren't in a group.
- **Cameras:** `latest` shows the newest camera alert; `featured` (Settings > Cameras) one camera.

`PUT /api/home/me` `{"cards": [{"id", "on"}…in order], "favourites": [ids ≤4]}` (either; any non-tablet
account; 403 when the admin switched off their "Can edit their own Home") → the new Home. `PUT /api/home/climate` `{"rooms": [{"entity", "name"}…in order]}` (household;
temperature devices only; default every temperature sensor) → the new Home. Live: `{"type": "home"}` on
the socket (at most once a second) whenever any of it changes; apps get it again. Settings key `home`.

Admins (web › Dashboards › Phone Home): `GET /api/home/layouts` →
`{"household": {"cards": [{"id", "on", "new", "title", "category", "screen"}], "favourites": [ids]},
"categories": [as /api/categories], "people": [{"userId", "name", "layout": "household"|"own", "canEditHome"}]}`
(no wall tablets) · `PUT /api/home/household` `{"cards", "favourites"}` (same checks as /me) ·
`POST /api/home/people/{uid}/reset` (drops their own layout) · `PATCH /api/home/people/{uid}`
`{"canEditHome": bool}` (admins always may: 400). Each answers like `GET /api/home/layouts` and sends
`{"type": "home"}` to whoever it affects.

## Categories

`GET /api/categories` (any signed-in) → `[{"id", "name", "builtIn", "screen", "count"}]` - the built-in ones
(kinds.CATEGORIES, with their `screen` role), then the ones made in Newman (`cat.<slug>`), each with how many
devices are in it now. Every device's JSON has `category` (where it is now: moved, else its kind's) and
`icon` (a name it was given, else null - draw its kind's icon). `GET /api/meta` has `"icons"`: every name a
device can be given (each kind's id, then generic ones) and `categories` with their `screen`.

- `PUT /api/entities/{id}/category` `{"category": id | null}` (admin) moves a device (null: back to its
  kind's); `{"newName": "Pool"}` moves it into a new category of that name (or the one already called that).
  A made-in-Newman category with nothing left in it is gone. → the device. Live: `entity`, `categories`.
- `PUT /api/categories/{id}` `{"name"}` (admin) renames a made-in-Newman category (400 for a built-in one).
- `PUT /api/entities/{id}/icon` `{"icon": name | null}` (admin) → the device.
- Moving a device changes where it's grouped and counted (Home cards, Devices, the category screens, Lights
  groups, the wall's suggested dashboard); what it does stays with its kind. Swapping a device carries both;
  removing it drops them. Settings key `categories`.

## House settings

`GET /api/settings/house` (any signed-in) → 
```
{"devices": {"batteryLow": 20, "leftOpenMinutes": 15},
 "lights": {"rampSeconds": 3.5, "step": 10, "holdSeconds": 0.5},
 "climate": {"range": [18, 26], "bands": [{"name": "Cool", "below": 21.5, "color": "#9db8ff"},
             {"name": "Comfortable", "color": "#f5b14c"}, {"name": "Warm", "above": 22.0, "color": "#ff9a4d"}]},
 "cameras": {"mode": "latest", "featured": null, "cooldownMinutes": 1, "clipDays": 3},
 "history": {"activityDays": 90, "runDays": 90}}
```
`PATCH /api/settings/house` (admin; any part) → the whole thing, or 400 `{"error"}`. Limits: batteryLow
5–50, leftOpenMinutes 1–240 (whole minutes), rampSeconds 3–6 (a full brighten/dim while held), step 5|10|20 (a tap's brightness step), holdSeconds
0.3–1.5 (how long a Pico button is held before it ramps), cooldownMinutes 0–1440 (camera automations' default
wait), clipDays 1–7 (alert videos kept), activityDays / runDays 1–90 (Activity and automation runs kept),
`featured` a camera, range low < high, 3 bands ascending with `#rrggbb` colours. Takes effect at once;
the socket gets `{"type": "settings-house"}`.

## Quiet hours

`GET /api/me/quiet-hours` · `PUT /api/me/quiet-hours` (the signed-in person's; 400 for an account with no
person) `{"enabled", "from": "22:00", "to": "07:00", "days": "all"|"weekdays"|"weekends", "allow": ["alarm",
"doorbell", "people", "urgent"]}` (default off, 22:00–07:00, every day, alarm + doorbell + urgent). A window
past midnight belongs to the day it starts. Inside it, that person's phones get every notification except
the allowed kinds silently: iOS without a sound at `interruption-level: passive`; Android data `"quiet": "1"`.
Kinds: **alarm** (an automation with the alarm wall style, or triggered by an urgent or alarm device),
**doorbell** (a doorbell sound), **people** (a camera's person detection), **urgent** (time-sensitive).

## People and phones

A **person** receives notifications; a **phone** is one registered app install.

`GET /api/people` → `[Person]` — the household (never guests; a guest gets `[]`), with where each one is
for those the requester may see (see [Where people are](#where-people-are)):

```json
[{"id": "p_dante", "name": "Dante", "sharing": true, "home": false, "place": "School", "distanceM": 2140.5,
  "lat": 43.668, "lon": -79.395, "accuracyM": 12.0, "at": 1790000000.0, "live": false,
  "color": "#3FAE7A", "photo": "/api/people/p_dante/photo?v=1a2b3c4d5e6f",
  "phones": [Phone], "automations": ["auto_1"]}]
```

`color` is always set: someone who never picked one gets the first colour of the hub's palette nobody
else has (kept from then on, so it's stable). `photo` is null without a photo; its `?v=` changes with
the picture, so it can be cached for as long as you like. Both go to everyone who sees the names (not
gated by location sharing). `live` is always `false` now (kept for older apps).

`POST /api/people` `{"name"}` → Person · `PATCH /api/people/{id}` `{"name"}` · `DELETE /api/people/{id}` (also deletes their photo)

**Colour and photo**

- `PUT /api/me/profile` `{"color": "#RRGGBB"}` → 200 Person (your own person; 400 for anything that
  isn't `#` + 6 hex digits; 404 for an account with no person, e.g. a guest). Admins:
  `PUT /api/people/{id}/profile` `{"color"}` for anyone.
- `PUT /api/me/photo` — the body is the picture itself (`Content-Type: image/jpeg|image/png|image/webp`,
  up to 3 MB) → 200 `{"photo": url}`. The hub crops its centre square, shrinks it to 256 px and keeps a
  JPEG under the data folder (`people/`, in backups). 415 other content types · 413 over 3 MB · 400 not
  a readable picture. Admins: `PUT /api/people/{id}/photo` (same body). `DELETE /api/me/photo` /
  `DELETE /api/people/{id}/photo` (admins) → `{"photo": null}`.
- `GET /api/people/{id}/photo` → `image/jpeg`, `Cache-Control: private, max-age=31536000, immutable`
  (signed in, not guests; 404 without a photo). Like music covers it also takes `?token=` for an
  `<img>` that can't send a header.
- Wall-tablet sessions can't change profiles (403).
- Live socket: a `person` message goes to every signed-in socket except guests' when a colour or photo
  changes (with what each may see of their location).

Phone: `{"deviceId", "name", "platform": "ios|android|test", "personId": "p_chris"|null, "registeredAt", "lastSeen"}`
`GET /api/phones` · `PATCH /api/phones/{deviceId}` `{"personId", "name"}` · `DELETE /api/phones/{deviceId}`

`POST /api/devices/register` (app launch):
`{token, platform, deviceId, friendlyName?, deviceModel?, apnsEnvironment?: "sandbox", personId?}` →
`{"ok": true, "personId": "p_chris"|null}`. An unassigned phone gets nothing until it is given a person
(the app asks "Who are you?" from `GET /api/people`; the dashboard can also assign it).

## Where people are

Each person's phone can share its location with the hub. Only the newest fix is kept (no history),
on the hub only. Sharing is set per account by an admin:

* `"choice"` (default) — the person switches it on in their app (off until they do); the household sees them.
* `"required"` — always on, they can't turn it off; the admin picks who sees them (`visibleTo`, default everyone).
* `"off"` — not shared; the app hides the switch.

Guests and wall-tablet accounts are always `"off"` (they never share). Guests never see anyone's
location (a guest isn't one of the people at all). A person always sees themselves.

**Wall tablets** see people by their account's `seesPeople` ("Can see where people are", default
`true`): with it on, a tablet sees everyone sharing by `"choice"`, and `"required"` people whose
`visibleTo` is `null` or contains the tablet's **account id**; with it off, nobody. A household member's
account signed in with a `tablet` session sees nobody (the wall shows the tablet's view, not theirs).

**My sharing (the phone app)**

`GET /api/me/location` →
`{"mode": "off"|"choice"|"required", "enabled": bool, "requiredBy": "Chris"|null, "everySeconds": 300, "live": false}`

`PUT /api/me/location` `{"enabled": bool}` → the same shape · 403 `{"error"}` when the mode is `"required"`
(turning it off: `"Required by Chris"`) or `"off"` · 400 without a boolean `enabled`. Turning it off deletes the kept fix.

`POST /api/me/location/report`
`{"lat": 43.668, "lon": -79.395, "accuracyM": 12.0|null, "at": 1790000000.0, "speedMps": 1.2|null, "source": "background"|"refresh"|"app"}`
(`"refresh"`: the one fix answering a locate push; older apps' `"live"` is still accepted)
→ always `{"live": false, "everySeconds": 300}` (there's no live following any more) ·
403 when this account isn't sharing · 400 bad body · 401 signed out. A fix older than the one kept is
ignored (still 200); `at` in the future counts as now.

`POST /api/me/location/push-token` `{"deviceId": "ios-…", "token": "<hex>"|null, "env": "sandbox"|"production"}`
→ `{"ok": true, "kept": bool}` — the iPhone app's **location push token** (from
`CLLocationManager.startMonitoringLocationPushes`, for its Location Push Service Extension), kept per
phone with the APNs environment it was made in (anything but `"sandbox"` is production). `deviceId`
must be this person's iPhone registered with `/api/devices/register` (404 otherwise — register first,
then send it). A `null` / empty `token` forgets it. 403 when this account isn't sharing · 400 for a
missing `deviceId` or a token that isn't hex. The hub also forgets a phone's token when the person
stops sharing, when the phone is removed, and when APNs says it's dead (410 / `BadDeviceToken` /
`Unregistered` / `DeviceTokenNotForTopic`).

**Seeing people**

`GET /api/people` (above): `sharing` false and every location field null (`home` false) for anyone the
requester may not see or who isn't sharing. `home` = inside Home's radius; `place` = the smallest named
place they're inside, else null; `distanceM` = metres from Home's centre (null until Home is placed).

`POST /api/people/{id}/refresh` → 202 `{"requested": true, "nextAllowedAt": epoch}` — someone tapped that
person: their phones get ONE **locate** push (below) and answer with one fix. At most once per 120 s per
person, whoever asks: otherwise 429 `{"error", "nextAllowedAt": epoch}` (no push; the earlier one's
answer is on its way). 403 when the requester can't see them, 404 unknown person. Wall tablets may post
it too (their Family map) for anyone they can see.

`POST /api/people/{id}/follow` → **410 Gone** `{"error"}` — live following was removed (it drained
batteries); older apps get this and carry on.

Live socket (`/api/ws`): `{"type": "person", "person": Person}` whenever someone's location, home /
place, sharing, colour or photo changes — location changes only to sockets allowed to see them (plus,
when someone stops sharing, to those who could see them a moment ago, with the location fields null).

**Locate push** (one per refresh, to that person's phones):
- iPhone with a location push token: APNs **location push** to that token — `apns-push-type: location`,
  `apns-topic: ca.nimaste.newman.location-query` (`APNS_BUNDLE_ID` + `.location-query`),
  `apns-priority: 10`, `apns-expiration` now + 60 s, payload `{}`, the token's own environment. It
  starts the app's LocationPushExtension even when the app was swiped away; it posts one fix with
  `source: "refresh"`. No repeats. If APNs refuses it, the silent push goes instead (and a dead token
  is forgotten).
- Other iPhones: silent APNs background push (`apns-push-type: background`, `apns-priority: 5`) with
  `{"aps": {"content-available": 1}, "newman": {"type": "locate"}}`.
- Android: an FCM data message `{"type": "locate"}` (skipped when FCM isn't configured).

**Admin: per account** — `GET /api/users` includes
`"location": {"mode", "enabled", "visibleTo": ["p_chris", "u_hallway", …]|null, "requiredBy": "Chris"|null}`
and `"seesPeople": true|false` (wall-tablet accounts; `null` for every other role);
`PATCH /api/users/{id}` `{"location": {"mode": "required", "visibleTo": ["p_chris", "u_hallway"]|null}}` (either key alone is
fine; `null` = the whole household and every tablet with `seesPeople`; the list holds person ids and
wall-tablet account ids — anything else is 400). Making it required records the admin as `requiredBy`; switching
off (or a guest / tablet role) deletes the kept fix.
`PATCH /api/users/{id}` `{"seesPeople": false}` — a wall tablet's "Can see where people are" (400 for a
non-tablet account or a non-boolean). The tablet's open sockets get a `person` message for everyone
straight away (filled in, or with the location fields null).
`PATCH /api/users/{id}` `{"name"}` (up to 48 characters) — renames the account and the devices named after
it (a wall tablet's battery and its “<name> music” player — device ids ending `.<account id>`) straight
away, except a device someone renamed themselves.

**Places**

`GET /api/places` (any signed-in account except guests) →
`[{"id": "home", "name": "Home", "lat", "lon", "radiusM": 150, "builtIn": true}, {"id": "pl_…", "name": "School", "lat", "lon", "radiusM": 250, "builtIn": false}]`

Home is built in: its centre starts at the weather connection's latitude / longitude (null until there is
one) with a 150 m radius. Admins: `POST /api/places` `{"name", "lat", "lon", "radiusM"}` ·
`PUT /api/places/{id}` (any of those; `home` takes `lat`, `lon`, `radiusM`) · `DELETE /api/places/{id}`
(not `home`: 400). Radius 25 m – 50 km; names up to 40 characters, not "Home".

**Map data**

`GET /api/map/token` (any signed-in account; signed out → 401) — which map to draw. With a MapKit JS key
set up (`MAPKIT_KEY_ID`, see README): `{"provider": "apple", "token": "<JWT>", "expires": <epoch s>}` —
an ES256 JWT, header `{"alg": "ES256", "kid": MAPKIT_KEY_ID, "typ": "JWT"}`, payload `{"iss": team id,
"iat": now, "exp": now + 30 min}`, for MapKit JS's `authorizationCallback`. No `origin` claim: the hub is
reached at several origins (LAN IP:port, the public name), so the token isn't tied to one. The same token
is handed out until it has under 5 minutes left, then a new one is signed. Without a key, or if the key
file can't be read (logged once): `{"provider": "openfreemap"}` — the maps then use the data below.
`Cache-Control: no-store`.

`GET /api/map/ofm/{path}` (any signed-in account) — OpenFreeMap's map data, fetched by the hub from
`https://tiles.openfreemap.org/{path}` and kept in `map-tiles/ofm/`: styles (`styles/positron`,
`styles/fiord`, … → JSON), the TileJSON (`planet`), vector tiles (`planet/<version>/{z}/{x}/{y}.pbf` →
`application/x-protobuf`), fonts (`fonts/{fontstack}/{range}.pbf`) and sprites (`.json` / `.png`). Every
link in a style or TileJSON is rewritten to `/api/map/ofm/…`, so a map loaded from the hub never contacts
anyone else. Sent gzip-compressed when the browser accepts it (plain otherwise). Tiles, fonts and sprites
are kept 30 days (browser `max-age` 7 days), styles and the TileJSON 6 hours (`max-age` 1 hour; a kept
copy is served at once and refreshed behind, and still served if OpenFreeMap can't be reached). Only that
one server: path segments are letters, digits and ` ._,@+-`, no `..`, known file types only — anything
else is 404; OpenFreeMap unreachable with nothing kept → 502.

### A remote's Controls

`attrs.buttons` on a remote (from its connection): `[{"id": "<event id>", "label": "Top" | "On" | "★" | "Button 3", "role": "on" | "off" | "raise" | "lower" | null}]`.
A remote with Controls has `controls` in its entity JSON (null otherwise):
`{"lights": [ids], "onLevel": 1-100 | null, "hold": bool, "rows": [{"key": "button_1" | "button_1_long", "label": "Tap top", "does": "On at 60%", "custom": false}]}`.

* `GET /api/entities/{id}/controls` (not wall tablets) → `{"controls": stored | null, "rows", "buttons", "choices": {"devices": [{id, name, kind, switchable, area, commands: [Command + holdOnly?]}], "automations": [{id, name}]}}`
* `PUT /api/entities/{id}/controls` (admins) `{"lights": [ids], "onLevel": 1-100 | null, "hold": bool, "buttons": {press: override}}` → like GET; 400 with a plain message when something's wrong.
  A press is the event it fires (`"button_1"` tap, `"button_1_long"` hold); an override is
  `{"do": "device", "entity", "command", "value"?}` (a "…while held" command only on a hold) |
  `{"do": "automation", "automation": id}` | `{"do": "nothing"}`. Presses without one do the standard thing.
* `DELETE /api/entities/{id}/controls` (admins) → like GET.

Settings key `remoteControls` holds them; `references` / `replace` count them (`"remoteControls"`,
counts `"controls"`). Deleting an automation a press runs leaves that press doing nothing (Activity says so).

## Automations

```json
{
  "id": "auto_1",
  "name": "Nightly lock check",
  "enabled": true,
  "trigger":   {"type": "time", "time": "21:00", "days": [0,1,2,3,4,5,6]},
  "condition": {"mode": "any", "entities": ["ring.f0b7…", "ring.6c76…"]},
  "notify": {
    "enabled": true,
    "title": "",
    "buttons": ["lock:ring.f0b7…", "lock:ring.6c76…", "lock_all", "snooze:60", "dismiss"],
    "onlyPending": true,
    "timeSensitive": true
  },
  "autoCommand": {"enabled": false, "command": "lock"},
  "recipients": ["p_chris", "p_marlina"],
  "followUp": {"enabled": true, "message": "{person} locked {devices}.", "remindMinutes": 30, "remindTimes": 3},
  "cooldownMinutes": null
}
```

**Who may change them:** `GET /api/auth/state` → `user.canAutomate` (admins always, wall tablets never,
others as an admin set with `PATCH /api/users/{id}` `{"canAutomate": bool}`). Every write - `POST`, `PUT`,
`PATCH` (enabled too), `DELETE`, `/run` - answers 403 `{"error": "Your account can't change automations"}`
otherwise; reading and `POST /api/automations/describe` stay open to every non-tablet account.

**Don't repeat for** - `cooldownMinutes`: 0–1440, or `null` = the default: no wait, except one started by a
camera detection waits Settings > Cameras `cooldownMinutes` (1 by default); `0` = never wait. A trigger
within the wait after a run is logged as a skipped run ("Not repeated: it ran 1 minute ago and waits 5
minutes between runs").

**Trigger ("When")** - a list of parts, any kinds mixed:

```json
"trigger": {"match": "all", "within": {"amount": 2, "unit": "minutes"}, "items": [
  {"type": "event", "entity": "frigate.garage", "event": "cat"},
  {"type": "state", "entities": ["lutron.device_6"], "to": "on"},
  {"type": "time",  "time": "21:00", "days": [0,1,2,3,4,5,6]}]}
```

| item `type` | fields | happens |
|---|---|---|
| `time` | `time` "HH:MM" 24 h, `days` 0=Mon…6=Sun; or `sun` `sunrise\|sunset\|dawn\|dusk` + `offset` minutes (negative = before; `time` is then ignored) | at that time (or that day's sunrise/sunset ± offset, from the home's location) on those days (a moment) |
| `state` | `entities` [ids], `to`: a state id or `"any"` | when those devices reach that state (true while it lasts) |
| `event` | `entity`, `event` (e.g. `"pressed"`, `"person"`) | when that device emits the event (a moment) |

`match: "any"` - any part starts it. `match: "all"` - it starts once every part is true together: states must
hold right now, moments (events/times) must have happened within `within` (default 2 minutes) of each other.
After an "all" start, the moments must happen again before it can start again.
The hub also returns a derived `type` (`time|state|event` when all parts are alike, else `mixed`) and mirrors the
first part of each kind into older flat fields (`time/days`, `entities/to`, `entity/event`) - write `items`.
**Condition** ("Only if") — `devices`: rows that must ALL hold:
`[{"entities": [ids], "state": "<state id>" | "active", "match": "any" | "all" | "none" | "always"}]`
(e.g. `[{"entities": ["lutron.device_6"], "state": "on", "match": "any"}, {"entities": ["ring.side"], "state": "LOCKED", "match": "any"}]`).
`"active"` = each device's own `activeState`. For a `time` trigger the devices in these rows are also what the
notification is about: the ones in the row's state become *pending* (they get buttons and the follow-up).
Older automations with flat `entities` + `mode` are read as one row with state `active`; the hub always
returns `devices` (and a derived flat `entities`) - write `devices`.
Optional limits: `between` `{"after": "19:00", "before": "06:00"}` (may wrap midnight) and
`days` `[0..6]` (Mon=0; `null` = every day; ignored for `time` triggers, which pick their own days).
`sun` `{"only": "dark" | "light", "sunset": minutes, "sunrise": minutes}` - only between sunset+offset and
sunrise+offset (dark, across midnight) or the other way round (daylight); `null` = no limit. Sun times need the
home's location (the Home place, else the Weather connection's); without one `errors` says so.
`dates` `{"from": "MM-DD", "to": "MM-DD", "year": null | YYYY}` - only on those dates, both ends included, by the
hub's local date. `year: null` = every year (`"12-01"`..`"01-06"` wraps over New Year; `"02-29"` is allowed and
counts as Feb 28 in other years); with a `year` it's that one range only (`to` before `from` = in the next year).
Missing / `null` = no date limit. All the limits combine (all must hold) whatever starts the automation; a run
outside them is saved as `skipped` ("Outside its dates…"). `nextRun` skips ahead to the range.

**Actions** — commands run when it fires, each on one or more devices:
`"actions": [{"entities": ["lutron.device_5", "lutron.device_6"], "command": "turn_on", "value"?, "for"?: {"amount": 2, "unit": "minutes|hours|days|seconds"}}]`.
With `for`, Newman puts each device back afterwards (unless someone changed it by hand). Older actions with a single `"entity"` still work.
`"command"` may also be one of the kind's `automationCommands`: `brighten_while_held` / `dim_while_held` on a
dimmable light ramp its brightness from the event that started the automation until that event's `ends` event
(`button_1_long` → `button_1_released`, a Pico's `raise_long` → `raise_released`) or 10 s, whichever is first. A light that can ramp by itself
(Zigbee2MQTT lights, Matter lights: Level Control *move*) gets one "start moving" and one "stop" and ramps
smoothly, bottom to top in about 3.5 s: Zigbee2MQTT `{"brightness_move_onoff": 73}` up /
`{"brightness_move": -73}` down / `{"brightness_move": "stop"}`; Matter `MoveWithOnOff` / `Move` / `Stop`.
Any other light is stepped by 10 % every 0.4 s (each step sent only after the last one), each step fading
into the next where the light can fade (a JSON-schema MQTT light gets `"transition": 0.4`), stopping at
100 % / 1 %. Either way brightening an off light turns it on low; dimming never turns a light off, and an off
light stays off. No `value` or `for`. Each run logs one `command` event ("Brightened Kitchen to 70%" - for a
light that ramps by itself, the level it reports once it stops).

**Live status** ("Show a live status on phones") — `"live": {"enabled": true, "entities": [ids], "title": "", "recipients": null | [person ids], "until": …}`
(`title` "" = the devices' names; `recipients` null = everyone). `until` is one of
`{"type": "leaves", "entities": [], "state": "active" | "<state id>"}` (until none of those devices — none listed = the live
status's own — is in that state; `active` = each one's `activeState`; if none is in it when the automation runs, nothing
starts), `{"type": "duration", "amount": 30, "unit": "seconds|minutes|hours|days"}`, or
`{"type": "trigger", "item": <a When part: time / state / event / property>}` (until that happens; at most 12 hours).
Running again while it shows keeps it (its clock restarts for a duration). Turning it off, disabling or deleting the
automation ends it. See [Live statuses](#live-statuses).

**Buttons** (order = order shown):

| id | shown as | does |
|---|---|---|
| `<command>:<entityId>` | "Lock Side Door" | runs that command on that entity |
| `<command>_all` | "Lock all" | runs it on every *pending* entity of the run |
| `snooze:<minutes>` | "Snooze 1 hour" | re-evaluates after N minutes; re-sends if still pending |
| `dismiss` | "Dismiss" | ends the run, no more reminders (an entity already confirmed still gets its follow-up) |
| `open` | "Open Newman" | opens the app (no server call) |

With `onlyPending`, a run only shows buttons for entities that were pending (e.g. unlocked) when it fired;
`*_all` is dropped when fewer than two are pending.

**Follow-up** — sent to all recipients per pending entity as each reaches the state its command
confirms to (e.g. LOCKED): `{person}` = who tapped (or "Someone at the door" when done by hand),
`{devices}` = the entity just confirmed ("Laundry"). Entities confirmed within 10 s of each other (Lock
all, or two by hand) share one follow-up (`{devices}` = "Side Door and Laundry"); when the last pending
one is confirmed, whatever is waiting goes out at once and the run resolves. A confirmed entity is
always told: if the run is dismissed or expires before its follow-up went out, it's sent then.
Reminders (`remindMinutes`, a whole number of minutes, null = never; the editor offers 15, 30, 60 and
120) name and offer buttons only for the entities still pending. `remindTimes` (1-5; null or missing = 3,
which is what older automations get) caps how many a run sends; saving anything else is a 400
(`Remind again 1 to 5 times`). A snooze (`snooze:N`) replaces the reminders still to come: one reminder
when it ends (not counted), then up to `remindTimes` more every `remindMinutes`. Runs expire after 12 h.
The summary says it: "If not, remind again every 30 minutes, up to 3 times." ("remind once more after
an hour" for 1).

**One notification per person per change** — another automation started by the same state change (same
entity, same new state, within 15 s) doesn't push to the people a follow-up tells about it (sent, or
queued to go out); it still runs (actions, Activity event, wall alert) and its run log says `Not sent to
Chris: covered by Nightly lock check's follow-up`; its other recipients are notified as usual. If it
gets the change before the run has (the run is still waiting for that entity), it's held for those
people (`Held for Chris: …`) and, 15 s later, sent to any of them no follow-up told after all (`Sent late
to Chris …`). Two automations with no run waiting both notify.

**Endpoints**

* `GET /api/automations` → `[Automation + {"summary", "lastRun", "nextRun"}]`
* `POST /api/automations` (body without id) → Automation · `GET|PUT|DELETE /api/automations/{id}`
* `PATCH /api/automations/{id}` `{"enabled": true|false}` → switches it on or off, answered as the list answers it (admins only; the phone apps' Automations list)
* `POST /api/automations/describe` (any automation body, unsaved is fine) →
  `{"summary": "At 9:00 PM every day, if any of Side Door or Laundry is unlocked, notify Chris and Marlina with Lock Side Door, … buttons. Once they are locked, tell everyone who did it.",
    "preview": {"title", "body", "timeSensitive", "buttons": [{"id", "label", "icon", "sensitive"}], "followUp": "Chris locked Side Door and Laundry."},
    "errors": ["Pick at least one device"]}`
* `POST /api/automations/{id}/run` `{"force": false}` → runs now (`force` skips the condition) → Run
* `GET /api/automations/templates` → `[{id, name, category, hint, automation}]` starter recipes
* `GET /api/runs[?automationId=]` → `[Run]`
* `POST /api/runs/{id}/press` `{"button": "lock_all" | "lock:<id>" | "snooze:60" | "dismiss" | …}` (any signed-in
  account: a wall tablet's alert card, the phone app's Alerts tab) → the same replies as the notification buttons
  below (device buttons answer `{"ok": true, "accepted": true, "commanding": [ids]}` at once). 409 when the run is
  gone, the button isn't one of the run's, or it needs Face ID / a PIN. A command that fails is pushed to the
  presser's person (from a wall tablet: the run's recipients).

Run: `{"id", "automationId", "name", "startedAt", "status": "sent|resolved|dismissed|skipped|failed", "pending": [ids], "resolvedBy": "Chris"|null, "log": [{"ts", "text"}]}`

## Voice

Wall tablets talk to Newman (README → Voice on the wall tablet). Any signed-in client may; a wall
tablet's session may use all of these except the settings.

`GET /api/voice` → `{"available": bool (speech-to-text configured), "speaks": bool (text-to-speech),
"settings": VoiceSettings, "wakeword": WakeWord | null}` for the signed-in account.

`POST /api/voice/utterance` — something said. Body: raw PCM (`Content-Type: audio/L16; rate=16000`,
16-bit little-endian mono; `?rate=` also works), a WAV file (`audio/wav`), or `{"text": "…"}`
(`application/json`: typed, no speech-to-text). Up to 30 s of audio; `?speak=0` skips the spoken reply.

```json
{"heard": "Turn off the kitchen lights.",
 "reply": "Okay, the Kitchen Lights are off.",
 "action": {"type": "command", "command": "turn_off", "entities": ["hue.k1", "hue.k2"], "value": null},
 "audio": {"type": "audio/wav", "data": "<base64>"},
 "timings": {"stt": 610, "intent": 2, "tts": 120, "total": 735}}
```

`action.type`: `command` · `refuse` (sensitive, `reply` "For safety, do that from the screen.") ·
`ask_state` · `ask_value` · `describe` · `where` · `today` · `show` (`show`: `camera` + `entityId` |
`cameras` | `page` + `pageId` | `person` + `personId` | `home` | `weather` | `calendar` | `todo` | `lyrics` (the
wall's player with the lyrics)) ·
`timer_set` (`timerId`) · `timer_query` · `timer_cancel` (`cancelled`) · `timer_list` · `stop`
(`stopped`: ringing timers stopped; with none ringing it pauses this tablet's music) · `music_play` (`music`:
what was asked for - `title`/`artist`/`album`/`playlist`/`query`/`kind` (`song`)/`shuffle`/`random`; the
reply says what plays, asks "Did you mean …?" when the name heard only fairly matches the library, or says
"I couldn't find …") · `music_answer` (`yes`: the answer to that question - a yes within 2 minutes plays it) ·
`music_control` (`command` of this tablet's music player, `value`) · `music_what` · `cook` (`cook`: what was done - `open` / `choose` (which one?) / `close` / `step` / `next` /
`previous` / `repeat` / `first` / `last` / `count` / `where` / `ingredients` / `how_much` / `scale` /
`servings_info` / `timer` (+ `timerId`: the "yes" to a step's timer offer) / `no_timer`; the screen follows
the `cook` push) · `automation_run` (`automation`: its id - "Run Good Night"; `refuse` with `automation` when
it would do something sensitive and the tablet doesn't allow those) · `confirm` ("Did you mean Massimo?" - a
name heard only fairly well: `name`, `slot` (`device` / `room` / `person` / `automation` / `recipe` / `page`);
"yes" within 60 s does the corrected request, "no" → `{"type": "confirm", "yes": false}`) · `ambiguous` ·
`unknown` · `none` (nothing was said; with `"retry": true` Whisper was restarting for a new hint and isn't
back yet - `reply` "One moment, I'm just getting ready. Please ask me again in a few seconds."; a request
during such a restart first waits up to 20 s for it). A request done with a misheard name put right has `action.corrected`
(the name meant, e.g. "Backyard"). `audio` is
null when text-to-speech isn't set up (or failed). 503 when speech-to-text isn't set up (audio sent),
502 when a speech service can't be reached. Nothing about the audio is kept; Activity gets an entry
(`kind: "voice"`, title `"Hallway: “…”"`, body the reply).

`GET /api/voice/wakewords` → `{"default": "hey_newman", "wakeWords": [{"name": "hey_jarvis", "phrase":
"Hey Jarvis"}, …]}`: every `<name>.onnx` in the data folder's `voice/` except the two feature models,
phrase from `<name>.json` (default: the name, “hey_jarvis” → “Hey Jarvis”).

`GET /api/voice/wakeword` (404 when the hub has no model) — for the wall app; the model this tablet
listens for (its `wakeWord` setting):

```json
{"name": "hey_newman", "phrase": "Hey Newman", "version": "3f2a…", "threshold": 0.5, "enabled": true,
 "files": {"melspectrogram": "api/voice/wakeword/melspectrogram.onnx",
           "embedding_model": "api/voice/wakeword/embedding_model.onnx",
           "wakeword": "api/voice/wakeword/hey_newman.onnx"}}
```

`threshold` already includes this tablet's sensitivity (low +0.15, high −0.15 on the model's own,
from `<name>.json`). `version` changes when any file, the chosen wake word or its threshold changes
(the wall page passes it to the app in `voiceConfig`; a new one makes the app download the files again). `GET /api/voice/wakeword/{name}.onnx`
serves the files (from the data folder's `voice/`).

**Timers** (per account): `GET /api/voice/timers` → `{"now": hub time, "timers": [Timer]}` ·
`DELETE /api/voice/timers/{id}` (cancel / stop ringing) · `GET /api/voice/timers/{id}/done` →
`{"text": "Your pasta timer is done.", "audio": {...} | null}`.
Timer: `{"id", "label" ("pasta" or ""), "name" ("pasta timer" / "10 minute timer"), "seconds",
"createdAt", "endsAt", "state": "running" | "ringing", "doneText"}`. Up to 10 per account, 24 h each; a
ringing one nobody stops is cleared after an hour. Changes are pushed to that account's sockets as
`{"type": "timers", "now", "timers"}`.

**Settings** (admin; per wall tablet account): `GET /api/voice/settings/{userId}` ·
`PATCH /api/voice/settings/{userId}` with any of
`{"enabled": true, "sensitivity": "low" | "normal" | "high", "volume": 0-100, "voice": "" | name,
"allowSensitive": false, "wakeWord": name}` (defaults shown; `voice` "" = the hub's `VOICE_NAME`;
`wakeWord` one of `GET /api/voice/wakewords`, read back as the one in use - the default when unset)
→ the settings, or 400.
The tablet's sockets get `{"type": "voice-settings"}`. `GET /api/voice/voices` →
`{"default": "en_US-lessac-medium", "voices": [{"name", "label"}]}` (the voices offered: Piper's, or with
`VOICE_TTS_KIND=openai` the speech service's English voices, e.g. `{"name": "af_heart", "label": "Heart
(American, woman)"}`; a tablet's `voice` must be one of them, and a saved voice the service doesn't have reads
back as "" - the default).

**The Whisper hint** (admin): `GET /api/voice/hint` → `{"text": "Newman. Chris, Dante, Backyard, …, Good
Night.", "tokens": estimated Whisper tokens (≤ 200), "words", "file": "/data/voice/whisper_prompt.txt",
"written": the file holds this text, "writtenAt": time or null, "whisper": {"container": "newman-whisper",
"auto": restarting it is on (VOICE_STT_RESTART), "state": "current" | "pending" | "restarting" | "failed" |
"stopped" | "missing" | "no-docker" | "docker-error" | "no-hint" | "starting" | "off", "pending": the file is
newer than the container's last start, "startedAt", "lastRestart": epoch seconds or null, "lastResult": "ready
in 14 s" | an error | null}}` - the names the Whisper container is told to expect, and whether it has read
them yet (Newman restarts it through the Docker connection when nobody is talking; README → Voice → The
Whisper hint).

**Teaching and training “Hey Newman”** (README → Voice → Teaching and training “Hey Newman”).
Recordings stay in the data folder (`voice/teach/<person>/positive|negative/<id>.wav`, 16 kHz mono);
the `newman-wakeword-trainer` container trains from them.

- `POST /api/voice/teach/start` (admin) `{"tablet": userId, "person": "Dana"}` → `{"ok", "person",
  "slug", "tablet"}`; that tablet's sockets get `{"type": "voice-teach", "person", "slug", "target": 30,
  "have"}` (it opens the teaching card). 400 when `tablet` isn't a wall tablet account or `person` is empty.
  A teaching session lasts up to 3 hours.
- `POST /api/voice/teach/stop` — from the tablet: ends its session; from an admin with `{"tablet"}`:
  ends it and pushes `{"type": "voice-teach-stop"}`.
- `POST /api/voice/teach/clips?person=Dana&kind=positive|negative` — one recording, body as for
  `/utterance` (raw PCM `audio/L16; rate=16000` or a 16 kHz WAV). Allowed from the tablet teaching
  that person (else 403 "Start teaching from the Newman dashboard first") or an admin.
  `positive` ("Hey Newman") 0.4–6 s, `negative` (ordinary talking) 5–35 s; it must have a voice in it
  (loudest 20 ms above ≈ -42 dBFS, enough of it louder than the room). → `{"ok": true, "clip": {"id",
  "kind", "person" (slug), "ms"}, "positive": n, "negative": n}` (that person's counts), or 422
  `{"ok": false, "error": "That was too quiet - move closer or speak up", "ms"}`.
- `GET /api/voice/teach` (admin) → `{"people": [{"slug", "name", "positive", "negative", "seconds",
  "clips": [{"id", "kind", "ms", "at"}]}], "totals": {"positive", "negative", "minutes"}, "minClips": 20,
  "target": 30, "teaching": [{"tablet", "person"}]}`.
- `GET /api/voice/teach/{slug}/{kind}/{id}.wav` (admin) — one recording.
- `DELETE /api/voice/teach/{slug}` (admin) — all of someone's recordings;
  `DELETE /api/voice/teach/{slug}/{kind}/{id}` — one (admin, or the tablet teaching that person: "Redo last").
- `GET /api/voice/train` (admin) →

```json
{"status": {"state": "idle|running|done|failed", "stage": "Training", "percent": 41.5, "startedAt": 1791069385,
            "finishedAt": null, "eta": 5400, "message": "", "candidate": null, "trainerOnline": true},
 "pending": false, "minClips": 20, "recordings": {"positive": 92, "negative": 3, "people": 3},
 "current": Metrics, "previous": Metrics | null,
 "candidates": [{"name": "hey_newman-20261003-231624", "createdAt": 1791070400, "metrics": Metrics}]}
```

  `trainerOnline`: the trainer's heartbeat (every 30 s while idle) is recent. Metrics: `{"threshold",
  "realRecall" (held-back real recordings caught, 0-1), "byPerson": [{"name", "recall", "clips"}],
  "realHeldOut", "syntheticRecall", "falseWakesPerHour" (on ~11 h of everyday audio), "steps",
  "durationSec", "trainedOn", "byThreshold": [...], "current": {the model in use measured on the same
  clips: "threshold", "realRecall", "byPerson", "syntheticRecall", "falseWakesPerHour"}}`; the installed
  model's also has `name` and `installedAt`. The first model (no metrics file) reads as
  `{"name": "hey_newman (first version)", "threshold": 0.5, "syntheticRecall": 0.43, "falseWakesPerHour": 2.5,
  "realRecall": null}`.
- `POST /api/voice/train` (admin) `{}` (or `{"steps": 2000}` for a short trial run) — writes a request
  for the trainer → the same as GET. 409 while one is running or waiting, 400 with fewer than 20
  "Hey Newman" recordings.
- `POST /api/voice/train/use/{candidate}` (admin) — installs it as `hey_newman.onnx` (+ `.json` with its
  threshold, `.metrics.json`), keeping the one in use as `hey_newman.previous.*`; every wall tablet's
  sockets get `{"type": "voice-settings"}` and the new wake-word `version` makes them download it.
- `POST /api/voice/train/undo` (admin) — swaps the installed and previous versions (404 without one).
- `DELETE /api/voice/train/candidates/{candidate}` (admin) — "Keep current": drop a new version.

Trainer files (`voice/train/` in the data folder, shared with the container): `requests/<id>.json`
(`{"id", "at", "by", "steps"?}`), `status.json` (as `status` above, plus `updatedAt`),
`candidates/hey_newman-<YYYYmmdd-HHMMSS>.onnx` + `.metrics.json`, `last-run.log`.

**Wall app ↔ page** (Android `window.NewmanWall` / `window.NewmanVoice`): the page calls
`NewmanWall.voiceConfig('{"enabled", "threshold", "version"}')` and `NewmanWall.voiceDone()` (the reply
finished - listen again); the app calls `NewmanVoice.event({type: "wake" | "level" (value 0-1) |
"audio" (pcm base64, rate) | "cancel" | "error" (message)})`. Teaching: `NewmanWall.recordClip(maxMs,
fixed)` records one clip with the same microphone setup (the wake word paused meanwhile; `fixed`
false: from the first word to the pause after it, 1–6 s, waiting up to 10 s for speech; true: `maxMs`
of audio, up to 35 s) and `NewmanWall.stopClip()` ends it (a fixed one hands over what it has, a spoken one is dropped); the
app answers `{type: "clip", pcm (base64 PCM16), rate: 16000, ms}` · `{type: "clip-level", value}` ·
`{type: "clip-none"}` (nobody spoke) · `{type: "clip-error", message}`.

## Activity

`GET /api/events?limit=&before=&kind=&since=&runId=` → `[{"id", "ts", "kind", "title", "body", "entityId", "data"}]`
(`runId`: the entries of one automation run - a push's `alertId`).

## Alerts

`GET /api/alerts?entity=&since=&limit=&before=` → camera alerts, newest first, each with `"at"`, `"label"`
("Person"), `"zone"` (first zone | null), `"snapshot"` and `"clip"` (signed-in API paths) added to what the
connection sends. `?entity=` one camera's; `?since=<unix>` from then on.
`GET /api/alerts?category=security[,garage]&since=` → the Activity entries about devices of those
categories: `[{"id", "at", "title", "body", "entity", "label", "zone", "snapshot", "clip", "kind"}]`.

## Integrations

`GET /api/integrations/catalog`

```json
[{"type": "lutron", "name": "Lutron Caséta", "category": "lighting",
  "description": "Pairs locally with a Smart Bridge…", "available": false,
  "fields": [{"key": "host", "label": "Bridge address", "type": "text", "required": true,
              "placeholder": "10.1.1.23", "help": "…"}]}]
```

Field `type`: `text | password | number | boolean | textarea | select | multiselect | devices`. `available: false` = coming later.
`options` holds fixed choices; `dynamic: true` means the choices come from `GET /api/integrations/{id}/options`
(`{fieldKey: [{"value", "label", "group"?}]}`). `advanced: true` = rarely changed, tuck under "More settings".
`devices` is a list of objects whose sub-fields are in `itemFields` - Reolink uses it:
`"devices": [{"host": "10.1.1.56", "name": "", "use": "everything|buttons"}]` with one shared username/password;
its options list Reolink cameras found at the addresses Frigate uses (group "Found" / "Already added").

* `GET /api/integrations` → `[{"id", "type", "name", "status", "detail", "entityCount", "config": {…secrets masked as "••••"}}]`
* `POST /api/integrations` `{"type", "name"?, "config"}` → Integration (starts it)
* `PATCH /api/integrations/{id}` `{"name"?, "config"?, "enabled"?}` — a masked secret sent back unchanged is kept
* `DELETE /api/integrations/{id}`
* `POST /api/integrations/{id}/reconnect` (admin) — starts it again with its saved settings; answers with the
  integration (as in `GET /api/integrations`: `status` `connected` / `error` + `detail`, or still `connecting`
  after ~10 s). Its devices stay meanwhile; any it no longer provides once connected stay too, `available: false`.
  400 when it's paused.

**Adding devices by setup code** (Matter). A catalog entry with `"addByCode": {"label", "placeholder", "help"}`
(null for every other connection) adds devices one at a time with a code:

* `POST /api/integrations/{id}/devices` `{"code"}` (admin) → `{"adding": {"state": "working", "message", "at"}}`
  straight away; the work goes on in the hub. 400 `{"error"}` in plain words when it's refused up front: not a
  setup code, a typo (a Matter manual code's last digit is a check digit), the Matter server isn't connected,
  or another device is still being added (cancel it first; a "working" older than the Matter server's own
  5-minute limit never blocks). The code is passed to the Matter server once and never stored.
* Progress, in `GET /api/integrations` for admins (not tablets), `adminDetails`:
  `"adding": {"state": "working" | "done" | "error" | "cancelled", "step"?: "looking" | "adding", "title"?,
  "message", "at", "startedAt", "elapsed"? (s, while working), "seen", "deviceIds"? }` (`title` is the step in
  words; `message` is the reason in plain words on error — an expired / used pairing-mode code, unreachable
  device, missing Thread route…; `seen` false = it ended and hasn't been shown yet),
* `POST /api/integrations/{id}/adding/cancel` (admin) → `{"adding"}` (state `cancelled`): Newman stops waiting
  and a new code can be tried at once. The Matter server can't be stopped part way, so a device that finishes
  pairing anyway simply appears. 409 when nothing is being added. `POST /api/integrations/{id}/adding/seen`
  (admin) — the ending has been shown (`seen` true).
  `"pairedDevices": [{"id", "name", "model", "manufacturer", "network": "Wi-Fi" | "Thread" | "Ethernet" | "",
  "available", "entityIds"}]` (one per Matter node; a node can be several Newman devices), and
  `"thread": {"ok": true | false | null, "routes": ["fd4a:…::/64 via fe80::… on br0"]}` (null: not readable).
* `DELETE /api/integrations/{id}/devices/{deviceId}` (admin) — one of `pairedDevices` off Newman's Matter fabric
  for good (the device leaves Newman's fabric; other apps that control it keep it). Its devices disappear from
  Newman. 404 unknown, 409 `{"error"}` when the Matter server refuses.

Matter devices report like any other: e.g. a plug is kind `switch` with `power` (W), `energy` (kWh), `voltage`
(V), `current` (A) when it measures them; a battery device has `batteryLevel` (%, as reported — may be a half
percent), `batteryLow` (the device says it's low or needs replacing) and `batteryType` ("CR2032 × 2"); `protocol`
is "Matter over Thread" / "Matter over Wi-Fi" / "Matter over Ethernet" when the device says which network it uses. The status `detail` ends with whether this computer can reach
a Thread network, and why not in plain words.

Frigate's settings: `hide_cameras` / `hide_zones` — Frigate camera / zone names left out (empty = everything,
including cameras added later). Older clients may still send `cameras` / `zones` (names to *include*, empty =
all): they're turned into the hide lists from Frigate's current cameras (kept as sent until Frigate can be read).

Docker's settings: `hide_containers` — container names left out (empty = every container, including ones
created later). Older clients may still send `containers` (names to *include*, empty = all): it's turned into
`hide_containers` from Docker's current containers (and still applies as an include list until Docker answers).
Containers younger than two minutes get no device yet. A container Docker no longer has at all goes (an
`entity-removed` message) after 10 minutes missing, or on the connection's first successful listing; one still
used by an automation, dashboard, phone layout or widget stays instead with `available: false`,
`attrs.removed: true` and `stateLabel` "Removed".

## Backups

Admins only. `GET /api/backups` → `{"backups": [{"name", "size", "created"}], "auto", "time", "keep"}` (the saved
ones in `data/backups/`, newest first; nightly at `time` "HH:MM" (default "03:30") while `auto`, the newest `keep`
(default 7) kept) · `PATCH /api/backups/settings` `{"auto"?, "time"?, "keep"? (1–90)}` → the settings; 400 with the
problem · `POST /api/backups` → the new saved backup · `POST /api/backups/download` `{"name"?: a saved one (default:
a fresh one), "passphrase"?: ≥ 8 characters, locks the file}` → the `.newmanbackup` file · `DELETE
/api/backups/{name}` · `POST /api/backups/restore` — multipart `file` (+ `passphrase`) or JSON `{"name"}`, with
`check: true` only reporting what's inside; otherwise the hub restarts into it, keeping what was there in
`data/.pre-restore-<time>/`. A backup is a zip: `manifest.json`, a consistent copy of `newman.db`, `.install_id`,
`.token_secret` (restored as `0600` unless `TOKEN_SECRET` is set in the environment), `integrations/`, `people/`,
`wall-pictures/`, `voice/` (without `train/requests/`), `matter/` (without `*.lock` / `*.pid`) and `zigbee/`
(without `log/`) - not the push keys, `whisper/`, `piper/`, `training/`, snapshots, clips, map tiles or logs. The
manifest also records each folder's owner (`"owners": {"matter": [1000, 1000], …}`), given back on restore. A
backup without `voice/`, `matter/` or `zigbee/` leaves those folders as they are.

## Snapshots

`GET /api/snapshots/{id}` (jpeg) · public: `GET /api/public/snapshots/{id}?device=&exp=&token=`

## Alert videos

`GET /api/clips/{integration}/{clip id}` (mp4; an alert's `clip` "frigate:r:<review id>" split at the
first `:`, each part URL-encoded) · public: `GET /api/public/clips/{integration}/{clip id}?device=&exp=&token=`
(the push's `clip_url`). Once the alert ended more than 30 s ago the clip is fetched from Frigate once,
kept for 3 days and served with byte ranges; before that it is streamed as Frigate builds it (no
length, no ranges, not kept). 404/502 while Frigate has no video yet. The phone apps download it
through their own connection and play the file.

## Notification buttons (public)

`POST /api/public/actions`
`{"button": "lock:ring.f0b7…", "subject": "<runId>", "deviceId", "exp", "token"}` →
`{"ok": true}` · 401 bad token · 409 `{"ok": false, "error"}` (button not part of that run, device gone…).

The reply never waits on a device. The token, the run and the button are checked first (401 / 409 at once);
Dismiss, Snooze and Open are done before replying (`{"ok": true}`, `{"ok": true, "snoozedMinutes": 60}`); a device
button (lock, lock all…) replies `{"ok": true, "accepted": true, "commanding": [entity ids]}` straight away and
sends its commands afterwards, so a slow or stalled connection (e.g. Ring's) can't make the phone time out. A
command that fails or isn't taken within 20 s is pushed back to the phone that pressed it - plain text, title
"Couldn't lock Side Door", body "Side Door didn't lock: <reason>" - and noted in the run's log and Activity
(problem). Success follows up as before (the follow-up once the devices report locked). Smart charging's "which
plug" answer is the same: checked at once, `{"ok": true, "accepted": true, "charger"}`, the plug switched after.
Every press is logged on arrival with its outcome (accepted / done / refused 401 / refused 409 and why).

The apps send a press to the hub's address in use (home or outside, with the client certificate and Cloudflare
headers), then its other address, and the push's `action_url` only as a last resort. The iPhone app sends each try
on a fresh connection, the first up to 20 s and the others up to 8 s, all within about 27 s and the background
time iOS gives it (it holds a background task for the press).

`POST /api/public/press-report` - after every press the phone app (iPhone and Android) reports what it saw, signed like the press
(`deviceId`, `subject`, `exp`, `token` of that push; accepted up to 7 days after the token expired, since the
phone queues a report it couldn't send and retries when the app is next opened) or with a signed-in session:
`{"id", "button", "subject", "deviceId", "exp", "token", "ok", "outcome", "summary", "appVersion",
"launchState", "sinceLaunchSeconds", "backgroundExpired", "actionURL", "at", "attempts": [{"at", "url",
"appState", "backgroundLeft", "timeoutSeconds", "ms", "status"?, "error"?: {"domain", "code", "description",
"underlying", "streamCode", "streamDomain", "failingHost", "path"}, "certChallenges", "certAvailable",
"otherChallenges", "metrics"?: {"transactions", "dnsStart", "dnsEnd", "connectStart", "connectEnd",
"secureStart", "secureEnd", "requestStart", "requestEnd", "responseStart", "responseEnd" (ms after the
fetch start), "remoteAddress", "remotePort", "localAddress", "networkProtocol", "reusedConnection",
"proxyConnection", "cellular", "expensive", "constrained"}}]}` → `{"ok": true}` (`"duplicate": true` when
the same `id` came already - the phone sends it to all the hub's addresses at once) · 401 bad token. URLs
are scheme, host, port and path only. A failed press is logged as one WARNING line per try ("Press report
from … - try 1/3 http://10.1.1.2:8093/api/public/actions at 21:00:04 (background, 29.4 s background left,
20 s allowed): 20004 ms, NSURLErrorDomain -1001 … ; cert challenges 0; dns - connect 1-? tls - …"); a press
that worked as one INFO line. Successful presses carry no `attempts`, only the `summary`. The Android
app sends the same fields (`appVersion` "Android …"): `error.domain` is the exception's class, `code` the
errno underneath (0 if none), `underlying` its causes and `path` the phone's network; `metrics` come from
OkHttp's events; `backgroundLeft` is always absent; `certChallenges` is 1 when the client certificate was
sent; `launchState` is the process state at the tap plus how long the work waited ("background +0.3s"),
and `backgroundExpired` means Android stopped the work (android/README.md → Push notifications).

`GET /api/diagnostics/press` (admin) - the last 50 press reports, newest first (in memory: gone after a
restart), without their tokens.

A button is only accepted if it was one of the buttons of that run, so a token can never trigger
a command the automation didn't offer.

## Push payload

Common fields (APNs custom keys / FCM data, all strings):
`kind` (`automation | followup | test`), `run_id`, `automation_id`, `title`, `body`,
`buttons` (JSON `[{"id","label","icon","sensitive"}]`), `snapshot_id?`, `snapshot_url?`, `action_url`, `action_subject`,
`action_exp`, `action_token`, `time_sensitive` (`"true"|"false"`), `thread`, `alertId` (the automation run
it came from: open it with `GET /api/events?runId=`; absent on pushes not tied to a run), `quiet` (`"1"` during
the person's [quiet hours](#quiet-hours) - post it silently).
Camera alerts also carry `clip`, `clip_url`, `frigate_event_id` (the review id) and `camera`; tapping
one opens that alert's video in the app.

iOS `aps.category` = `run.category` (`c_` + a hash of the button set). The app registers every set the hub
can foresee from `GET /api/push/categories` (`[{"id": "c_…", "buttons": [...]}]`, refreshed on launch and
on coming to the foreground, and on any background wake - a silent push or a location event - at most
every 15 minutes). Only the app registers categories: a set it hasn't fetched yet shows without buttons
until it has. `aps.mutable-content: 1` is set only for pushes with a snapshot (the Notification Service
Extension attaches it and never touches categories: on iOS 26, an extension registering a push's category
stopped every later notification from showing until the phone restarted), and those pushes leave out `buttons`.

The silent **locate** push (someone tapped you on a map: one fresh fix) carries no alert: see
[Where people are](#where-people-are). Test phones get `{"kind": "locate", "type": "locate"}` in the outbox.

## Test phones (development)

Platform `test` devices don't push anywhere: every notification they'd get is kept in
`GET /api/test/outbox?deviceId=` (newest first, full payload incl. tokens) so scenarios can be
run end-to-end from a script or the dashboard. `DELETE /api/test/outbox` clears it.
