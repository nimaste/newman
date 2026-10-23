<h1 align="center">Newman</h1>

<p align="center">
  A small, self-hosted home hub — locks, alarm, doorbell and cameras, lights, garage, thermostat,
  vacuum, appliances, containers and more — with a web dashboard, a wall-tablet view and
  native iOS / Android apps (phone and wall tablet). Built to replace Home Assistant without needing it for anything.
</p>

---

## Contents

- [What Newman is](#what-newman-is)
- [Quick start](#quick-start) — installing (Unraid, Debian / Ubuntu, TrueNAS SCALE, Synology) and upgrading
- [Signing in and security](#signing-in-and-security)
- [Connections](#connections) — every device type, how it connects, what it needs (and [camera video settings](#camera-video-settings), [music from Navidrome](#music-navidrome), [recipes from Forkast](#recipes-forkast))
- [Matter and Thread](#matter-and-thread) — the Matter server, the host's IPv6 settings (Unraid, Debian, TrueNAS, Synology), adding devices shared from Apple Home or Google Home
- [Companion containers](#companion-containers) — Docker socket proxy, speech (Whisper, Piper, Kokoro), the wake-word trainer, Matter server, Zigbee (Zigbee2MQTT with an SLZB-06), Mosquitto
- [Voice on the wall tablet](#voice-on-the-wall-tablet) — “Hey Newman”, what it understands, alarms and timers, teaching it your voices
- [Where people are](#where-people-are) — location sharing, places, the Household map
- [Automations](#automations)
- [Remotes and their Controls](#remotes-and-their-controls) — what a remote's buttons do, without automations
- [Upkeep and Today](#upkeep-and-today)
- [Dashboards and the wall tablet](#dashboards-and-the-wall-tablet) (and [phone layouts](#phone-layouts), [wall tablet battery and smart charging](#wall-tablet-battery))
- [Apps](#apps) — the phone and wall apps, connecting them, [the phone's Home](#the-phones-home) (groups, rooms, quiet hours), [Settings](#settings), widgets, live statuses
- [Deploying behind Caddy](#deploying-behind-caddy)
- [Settings (environment variables)](#settings-environment-variables)
- [Data and backups](#data-and-backups)
- [Development](#development) (and [releases](#releases))

## What Newman is

One container (~280 MB image, ~100 MB of RAM in use) holding:

- **The hub** (Python 3.12, asyncio + aiohttp, SQLite) — talks to every device, runs automations,
  sends push notifications, serves the API.
- **The web dashboard** (React, built with Vite) — devices, automations, connections, accounts,
  dashboards, activity, live cameras.
- **The wall tablet view** at `/wall` — a full-screen, swipeable, touch-first view of a dashboard.

Everything a client shows is driven by the hub's own description of each kind of device (what it
reports, what it can do, how to word it), so a new kind of device never needs screens hand-built
for it — the dashboard, wall tablet, automation builder and phones all render it generically.

Wherever possible Newman talks to devices **directly on your network** (Lutron, HomeKit, Reolink,
Shelly, Kasa, Hue, ESPHome, Sonos, Meross, printers, Roborock when it can). Where a maker only offers
a cloud service (Bosch/Thermador Home Connect, LG ThinQ), Newman keeps **only an access token, never
your password**.

## Quick start

You need Docker with the Compose plugin (`docker compose version`), on a Linux machine (host networking,
below). Then, in a folder holding this repository's `docker-compose.yml` — every setting is written out
in it, so it's the one file to edit:

1. **Pick the data folder.** `docker-compose.yml` uses Unraid's `/mnt/cache/appdata/newman` (data, mounted
   at `/data`) and its `keys/` subfolder (push keys, mounted read-only at `/app/keys`). Elsewhere, change both
   `volumes:` lines (and the companions' `/mnt/cache/appdata/...` folders) to a folder of yours, e.g.
   - **Unraid**: as is. `docker compose` comes with the *Docker Compose Manager* plugin (Community Apps).
   - **Debian / Ubuntu**: `/opt/newman` (Docker's own `docker-compose-plugin` package gives `docker compose`).
   - **TrueNAS SCALE** (24.10+, Docker): a dataset such as `/mnt/<pool>/apps/newman`; run `docker compose`
     from *System → Shell*, or install it as a YAML custom app (*Apps → Discover Apps*, keeping
     `network_mode: host`).
   - **Synology DSM 7.2+**: `/volume1/docker/newman`; *Container Manager → Project → Create* with this
     `docker-compose.yml`, or `sudo docker compose` over SSH.

   ```bash
   mkdir -p /mnt/cache/appdata/newman/keys      # your folder from above
   ```
2. **Settings:** edit the `environment:` block of the `newman` service in `docker-compose.yml` directly —
   each setting has a comment, and the lines marked `CHANGE` hold one household's own values to replace
   with yours: at least `TZ` and `PUBLIC_URL`; comment out the `APNS_*` / `MAPKIT_KEY_ID` ids if you have
   no Apple keys, and the `VOICE_*` lines if you don't run the voice containers (see
   [Settings](#settings-environment-variables)). Connections — MQTT, Reolink and the rest — aren't set
   here: they're added in the dashboard under *Connections*. No secrets go in the file: the
   secret that signs notification links is made by Newman on first start (`.token_secret` in the data
   folder, in its backups), and passwords are entered in the dashboard.
3. **Push keys (optional):** the iOS APNs key as `keys/apns-key.p8` (its id and your team id are
   `APNS_KEY_ID` / `APNS_TEAM_ID` in `docker-compose.yml`) and, for Android, Firebase's service account as
   `keys/firebase-service-account.json` (then uncomment `FCM_SERVICE_ACCOUNT_PATH` in `docker-compose.yml` —
   Newman doesn't start while it points at a missing file). Without them everything works except push
   notifications.
4. **The image:** `ghcr.io/nimaste/newman` is published by this repository's release workflow. While the
   repository is private, so is the image: `docker login ghcr.io` once with a GitHub token that has
   `read:packages`. Or build it yourself: swap `image:` for `build: .` in `docker-compose.yml`.
5. **Start:** `docker compose up -d` (Newman and the Docker socket proxy; the other companions have their
   own profiles — see [Companion containers](#companion-containers)). `docker compose logs -f newman`, or
   the data folder's `logs/newman.log`, shows it starting.

**Upgrading:** `docker compose pull newman && docker compose up -d newman` (built yourself: `git pull &&
docker compose up -d --build newman`). The data folder carries over; take a backup first (*Backups*, see
[Data and backups](#data-and-backups)). Open screens (browsers, wall tablets) load the new web build by
themselves. When a new version's `docker-compose.yml` adds settings, copy them into yours (`git diff`
shows them) — your edited values stay yours.

*Coming from an install that used a `.env` file:* move each of its lines into the `environment:` block of
`docker-compose.yml` (as `NAME: value`) and drop `env_file:`. For `TOKEN_SECRET`, either write its value
into the data folder as `.token_secret` (`printf '%s\n' '<value>' > .token_secret && chmod 600 .token_secret`)
or keep it as `TOKEN_SECRET:` in `docker-compose.yml` — with the same secret, links in notifications already
sent keep working. Left out, Newman makes a new one on its next start.

Open `http://<your server>:8093` from a device at home (first-time setup is refused from outside).
The first account you create is the admin. Then go to
**Connections**: anything Newman found on your network is listed under *Discovered* — one click to set
it up — and **Add** lists everything else.

Newman needs **host networking** (`network_mode: host`, as in the compose file): device discovery
(mDNS), HomeKit, Sonos, Hue, Matter and printers all rely on multicast, which doesn't cross Docker's
bridge network.

## Signing in and security

- **Accounts** — admin, family, guest and wall-tablet roles. Admins manage connections, accounts and settings; family
  members use devices, automations and dashboards. A **guest** uses the house like family but isn't one of
  the people — never notified, never on People, can't see where anyone is. Sessions use a cookie (browser) or a Bearer token
  (apps); passwords are stored with scrypt. The dashboard and the wall page have separate cookies, so signing in on
  the wall page in a browser never signs that browser's dashboard out (or the other way round).
- **Home vs. away** — a request from a private address (your LAN, your home VPN) is *home*, whether
  it comes straight to Newman or through the reverse proxy (matching the proxy's own "no certificate
  needed on the LAN" exclusion). Everything else is *away*. Only the reverse proxy's address range is
  trusted to say who the client is (`X-Forwarded-For`, default `172.16.0.0/12`, Docker's; change it
  in *Settings → Sign-in*), and only the address the proxy itself added counts.
- **Certificates** — Newman only counts a real fingerprint in `X-Client-Cert-Fingerprint`; the
  placeholder text Caddy passes when no certificate was presented is ignored.
- **Away sign-in policy** (admin setting): `cert_or_2fa` (default — a client certificate *or* a
  2FA code), `cert`, `2fa`, or `password`. Client certificates are checked by Caddy (mTLS), which
  passes the fingerprint and subject to Newman in `X-Client-Cert-Fingerprint` /
  `X-Client-Cert-Subject` (see [Deploying behind Caddy](#deploying-behind-caddy)).
- **2FA** — optional per account, standard TOTP (any authenticator app).
- **Wall tablet** — give each tablet its own **Wall tablet** account (Accounts → role "Wall tablet"):
  it signs in only on a wall tablet, has its own dashboard (an admin lays it out), can use devices but
  never change settings, and isn't a person — never notified, never on People. Whether it can see
  where people are is its own setting (*Can see where people are*, on by default — see
  [Where people are](#where-people-are)). The wall page signs in only with a wall tablet account:
  a person's own account is refused there (*This page is for wall tablet accounts - sign in with the
  tablet's own account (add one under Accounts)*), and a wall sign-in left from before that, or of an
  account whose role is changed from Wall tablet, ends (its battery and music player go). To switch
  accounts, press and hold the clock on the wall screen → **Sign out**. Renaming the account (the pencil by its name, or
  **⋯ → Rename…**) renames its battery and music player too — unless you renamed those yourself.
  Both last only while the account is signed in on a wall tablet: signing it out there removes them
  ("Removed Chris's tablet: no longer signed in on a tablet" in Activity; see
  [Wall tablet battery](#wall-tablet-battery)).
- **Alerts on wall tablets** — an automation's **Then** can also *show it on a wall tablet*: as a card
  that slides in, full screen with the camera live (e.g. the doorbell), or as an alarm that stays until
  someone taps OK; with or without a sound, waking a dimmed screen or not. It reaches the tablet over
  its live connection, so it needs no push service (Fire tablets included). Whether unlock / disarm / open-garage on the tablet needs
  nothing (default), an "Are you sure?", or a PIN is an admin setting.
- **Sensitive commands** (unlock, disarm, open the garage, sound a siren) are marked as such so the
  phone apps can ask for Face ID / fingerprint. A notification button can only run what its automation
  offered (lock, lock all, snooze, dismiss…), and a sensitive one asks for Face ID / fingerprint first.
- **Secrets in connection settings** (keys, tokens) are never sent back to the browser — forms show
  `••••`. Webhook addresses, which contain their secret, are shown to admins only.
- **Maintenance** — `data/.local_token` lets scripts on the server itself call the API
  (header `X-Newman-Local-Token`); it's only accepted from `127.0.0.1`.

## Connections

Status: ✅ in daily use · 🧪 tested against the real thing, new · β built and tested offline, not yet
tried on real hardware.

| Connection | Devices | How it connects | You'll need |
|---|---|---|---|
| ✅ **MQTT devices** | locks, alarms, sensors, leak/smoke, sirens, plugs, lights, blinds, fans, garage doors, buttons | Home Assistant–style MQTT discovery: ring-mqtt, Zigbee2MQTT, Z-Wave JS UI, Tasmota, ratgdo… | An MQTT broker (Mosquitto) |
| ✅ **Reolink** | cameras, doorbells, NVRs; button presses, motion and AI detection, snapshots, floodlights | Directly to each device (Baichuan push) | Camera address, user, password |
| ✅ **Frigate** | cameras and zones; detections with snapshots, live counts, live video, alerts | Frigate's API + its MQTT events | Frigate's address |
| ✅ **Lutron Caséta** | dimmers, switches, Pico remotes (press, long press, let go) | Locally to the Smart Bridge (LEAP) | Press the bridge's button once |
| ✅ **Meross garage opener** | garage doors | Locally; the cloud is used once to fetch the device key | Meross sign-in once (the password isn't kept) |
| ✅ **HomeKit device** | e.g. Ecobee thermostat + room sensors | HomeKit Accessory Protocol, locally | The 8-digit HomeKit code; remove it from other hubs first |
| ✅ **Weather** | conditions, hourly / 7-day forecast, Environment Canada warnings | Open-Meteo (free, no account) | Nothing |
| ✅ **Calendars** | upcoming events, "event starts" | Private iCal links (Google, iCloud, Outlook…) | The calendar's secret iCal address |
| ✅ **Calendar server (CalDAV)** | every calendar the account can see; the wall tablets add and change events; reminders on the wall | CalDAV (GCloud, Nextcloud, Radicale, iCloud…) | Server address, user name and an app password |
| ✅ **Roborock** | vacuums: start, pause, dock, find, clean rooms; battery, consumables | Locally when it can, Roborock's cloud as fallback | Email code sign-in once (session kept, no password) |
| 🧪 **Home Connect** | Bosch, Thermador, Siemens, Gaggenau, Neff appliances | Home Connect's API + live event stream | A free developer app (Client ID) — see below |
| β **LG ThinQ** | LG washers, dryers, WashTowers, dishwashers, ovens, fridges | LG's official ThinQ Connect API | A personal access token — see below |
| β **Ring** | the Ring alarm live: arm / disarm, locks, door and window, motion, smoke / CO and leak sensors, sirens, keypads; who did it (the person or keypad code the Ring app shows) | Directly to Ring, like the Ring app (its live connection) — replaces the ring-mqtt container | Ring sign-in once (email, password, texted code; only the token is kept) — see below |
| 🧪 **Docker** | containers: state, health, CPU, memory; start / stop / restart | Docker API, live events | A socket proxy — see [below](#docker-socket-proxy) |
| β **Unraid** | the server (CPU, memory, uptime, array, parity checks) and every disk (how full, temperature, errors, asleep) | The official Unraid API (GraphQL, built into Unraid 7) | An API key, role “viewer” — see [Servers](#servers-unraid-linux-computers-proxmox) |
| β **Linux computer** | a Debian / Ubuntu / Raspberry Pi OS machine: CPU, load, memory, temperatures, uptime, each disk | Glances (or node_exporter) running on it | [Glances](#servers-unraid-linux-computers-proxmox) on the machine |
| β **Proxmox VE** | nodes, storage, disks (SMART), VMs and LXC containers: start / shut down / reboot | Proxmox's API | An API token — see [Servers](#servers-unraid-linux-computers-proxmox) |
| 🧪 **Printer** | ready / printing, toner and ink levels, paper / jam / cover alerts | IPP (AirPrint / Mopria), found automatically | Nothing |
| β **Shelly** | relays, plugs, dimmers, blinds, sensors, power | Locally (Gen1 polled, Gen2+ instant) | Nothing |
| β **TP-Link Kasa / Tapo** | plugs, strips, switches, dimmers, bulbs, power | Locally | Tapo / newer Kasa: TP-Link sign-in |
| β **ESPHome** | anything running ESPHome (e.g. ratgdo) | Native API, locally | Encryption key if set |
| β **Philips Hue** | lights, motion sensors, remotes | Locally to the bridge | Press the bridge's button |
| β **Matter** | any brand's Matter devices — plugs (with power and energy), lights, motion, leak, door / window, temperature and humidity sensors, buttons, locks, blinds, thermostats, fans; battery level and "replace the battery" — over Wi-Fi, Ethernet or Thread | Newman's own Matter fabric, through the Open Home Foundation's Matter server (matterjs-server); devices shared from Apple Home / Google Home with their pairing-mode code | The Matter server container; for Thread devices a Thread border router (Apple TV, HomePod, Nest hub) and the host's IPv6 settings — see [Matter and Thread](#matter-and-thread) |
| β **Sonos** | speakers: play, pause, volume, what's playing | Locally | Nothing |
| 🧪 **Navidrome** | your own music on the wall tablets' speakers: one music player per tablet — play / pause / next / previous / stop, volume, “play something” by search, shuffle an artist or playlist; what's playing | Navidrome's Subsonic API (any Subsonic server works); the audio streams through the hub to the wall page | Username and password **once** — Newman keeps only a token (see [Music](#music-navidrome)) |
| 🧪 **Forkast** | your recipes in **cook mode** on a wall tablet — “pull up the pancakes”, steps one by one, ingredients and amounts, scaling, timers from the steps — by voice or touch; one *Recipe book* device (how many recipes) | Forkast's API (read only) | Email and password **once** — Newman keeps only Forkast's sign-in token (see [Recipes](#recipes-forkast)) |
| β **Webhooks** | addresses that start automations, and that Newman can call | HTTP | Nothing |
| 🧪 **Wall tablets** | each wall tablet's own battery: level, charging, temperature, voltage; smart charging from its plug (20–80 %) | The Newman Wall app reports it (made by itself the first time) | Nothing — see [Wall tablet battery](#wall-tablet-battery) |
| β **AdGuard Home** | network ad blocking: on / off / pause N minutes, queries and blocks today, clients, rules, updates; "turned off" / "back on" | AdGuard Home's API, every 30 s | A user and password — AdGuard Home has no API tokens; make a separate user for Newman — see [DNS filters](#dns-filters-adguard-home-pi-hole-technitium) |
| β **Pi-hole** | the same, for Pi-hole v6 (v5 too) | Pi-hole's API, every 30 s | An app password (a revocable key, not your sign-in password) |
| β **Technitium DNS** | the same, for Technitium DNS Server | Technitium's API, every 30 s | An API token |
| β **Hunter Hydrawise** | sprinkler controllers and zones: what's watering, time left, next run; water a zone for N minutes, stop, rain delay, standby | Hydrawise's API, polled as often as Hydrawise allows (≥ 1 min) | The account's API key — see [Sprinklers](#sprinklers) |
| β **Rain Bird** | sprinkler controllers and zones: what's watering, rain sensor, rain delay; water a zone for N minutes, stop, rain delay | Locally to the LNK / LNK2 Wi-Fi module (pyrainbird), every minute (20 s while watering) | Controller address and its password — Rain Bird has no tokens, so the password is kept (masked) |
| β **Rachio** | sprinkler controllers and zones: what's watering, time left, last / next run; water a zone for N minutes, stop, rain delay, standby | Rachio's public API, polled within its 1,700-a-day limit; optional instant webhooks | The account's API key — see [Sprinklers](#sprinklers) |
| β **Network checks** | one device per check: a ping, a TCP port or a web address (status code / a word on the page) — online / offline, response time, uptime over 24 h; "goes offline" / "comes back" | From the hub, every 60 s per check (your choice); ping without special rights, TCP as fallback | Nothing — see [Network checks](#network-checks) |
| β **Amazon Alexa** (experimental) | no devices: the timers running on your Echos, shown on the wall tablets with Newman's own (name, countdown, which Echo; "Done · Ringing on …" at zero) | Amazon's unofficial connection, the one the Alexa app uses — it can stop working whenever Amazon changes things; timers checked every 30 s | Amazon sign-in once on Amazon's own page (only Amazon's token is kept) — see [Amazon Alexa](#amazon-alexa-experimental) |
| β **Sun** | the Sun: up / down, next sunrise / sunset / dawn / dusk, height and direction in the sky; sunrise, sunset, dawn, dusk events | Worked out on the hub (NOAA's equations), nothing online | Your home's location (the Home place, or the Weather connection's) — see [Sun](#sun) |
| 🧪 **Roku TV** | Roku TVs and players (TCL, Hisense, onn… Roku TVs): on / off, volume up / down, inputs, apps, keys, what's open | Roku's local control (ECP, port 8060), found automatically | Nothing — see [TVs](#tvs) |
| β **Samsung TV** | Tizen TVs (2016+): on / off, volume, inputs, apps, keys | The TV's remote websocket (8002) + REST (8001) + UPnP volume, found automatically | Allow Newman on the TV once (only the TV's key is kept) |
| β **LG TV (webOS)** | on / off, volume, inputs, apps, keys, playback; live updates | The TV's SSAP websocket (aiowebostv), found automatically | Accept Newman on the TV once (only the client key is kept) |
| β **Android TV / Google TV** | Google TV, Chromecast, onn, TCL / Hisense / Sony Google TVs, Shield: on / off, volume, apps, keys, current app | Android TV Remote v2 (androidtvremote2), found automatically | The code the TV shows, once (only Newman's own certificate is kept) |
| β **Apple TV** | on / sleep, keys, volume, apps, what's playing | pyatv (AirPlay + Companion), found automatically | Two codes the Apple TV shows, once (only the pairing credentials are kept) |
| β **Hisense TV (VIDAA)** | VIDAA Hisense / Toshiba TVs: on / off, volume, inputs, apps, keys | The TV's own MQTT broker (36669, TLS) — community-researched | The code the TV shows, once; some sets need a client certificate — see [TVs](#tvs) |
| **Virtual test devices** | pretend locks, doors and a doorbell | — | Nothing |

### How often Newman checks

Connections that are told about changes as they happen (Ring, Lutron, Hue, HomeKit, Matter, ESPHome,
Reolink, Frigate) also ask for everything again now and then, in case a change was missed. That's
**Check everything every (minutes)** under a connection's More settings. Newman only ever asks the hub,
bridge or server that already holds the status - battery devices are never woken by it.

| Connection | Default | Lowest | What it asks again |
|---|---|---|---|
| Ring | 30 | 5 | the alarm base station, for every device's status (it answers from what it holds) |
| Lutron | 15 | 1 | the bridge, for every dimmer's and switch's level (Pico remotes are only listened to) |
| Hue | 15 | 1 | the bridge, for everything; only what changed comes through |
| HomeKit | 1 | 1 | a Wi-Fi accessory, for all its values; Bluetooth and Thread ones are only listened to |
| Matter | 15 | 1 | the Matter server's own copy of every device (devices themselves aren't asked) |
| ESPHome | 15 | 1 | a connected device, to send all its states again (deep-sleep devices are skipped) |
| Reolink | 15 | 1 | each device's names, firmware and floodlights |
| Frigate | 10 | 1 | Frigate's cameras and zones |

Connections that only check on a timer have their own **Check every…** setting: Hydrawise (60 s, at
least 60, never sooner than Hydrawise asks), Rachio (180 s, at least 180; slower with several
controllers to stay within Rachio's daily limit), Rain Bird (60 s, at least 20; every 20 s while
watering), Weather (the weather every 15 min, at least 5; warnings every 5 min, at least 1), and the
TVs, Kasa, Meross, Sonos, Shelly, DNS filters and calendars as before. Saving less than a setting's
lowest value is refused.

### Muting a device's problem

A device that reports a problem (offline, low battery, no AC power, tamper, jammed…) shows in Needs
attention, the To do list and the chips at the top. Any one of those problems can be muted, on its own:
on the device's page (**Mute ▾**), in the wall tablet's Needs attention sheet, a To do row's **Mute**, or
the device's sheet on the wall - for **1 day**, **1 week**, **until it's fixed** (it stays quiet while
the problem lasts and comes back if it clears and returns) or **always**. Only that problem is muted: a
keypad whose "No AC power" is muted still says when its battery runs low. Muted problems stay listed,
in grey with who muted them and until when, on the device's page, with **Unmute**. Anyone signed in -
wall tablets too - can mute; the Activity log says who did.

Admins can also **switch a warning off** for good on a device's **Warnings** tab - every problem that
device can report, each with a switch - and give it **its own low-battery level** (5–50 %) instead of the
house's (Settings › Devices, 20 % to start). A door, window, lock or garage can also get its own **left open**
time (1–240 minutes) before Home says it needs you, instead of the house's (Settings › Devices, 15 minutes). A switched-off warning never shows anywhere: not in Needs
attention, To do, the chips or the phone's Home.

### Reconnecting a connection

Every connection's page has **Reconnect** (also in its ⋯ menu, admins only): it starts the connection
again with its saved settings, nothing changed, and says whether it came back. Its devices stay put while
it does; any it no longer provides once it's back (a camera removed at the source, say) are kept and shown
unavailable, never deleted, so automations that use them keep pointing at them.

### Finding and removing devices

**Devices** narrows the list by category (and type), **status** — *Online*, *Offline or unavailable*,
*Needs attention* (offline, low battery, tamper, no power… the same as the *Needs attention* tile) —
**connection** (every installed one by its name) and a name or room search, each option with how many
devices it would show alongside the other filters. The filters live in the page's address, so Back from a
device returns to the same list.

In *Tiles* each category is one row that sums it up — how many devices and a few status chips worked out from the
kinds (*All locked*, *Side Door unlocked*, *Alarm disarmed*, *Motion: Master Bedroom*, *2 on*, *Main Floor · Heat*,
*Basement 21.1 °C*, *1 offline*) — and opens to its device tiles. A category with something needing a look
(offline, unlocked or open, a leak, a battery or other problem) starts open, the rest closed; opening or closing one
(or *Expand all* / *Collapse all*) is remembered in that browser. Searching or filtering opens every category with a
match. *Table* lists every device in one table.

Admins can remove devices that are offline or unavailable: with the status filter on *Offline or
unavailable*, the tiles and table rows can be picked (*Select all* picks the whole list), then
*Remove N devices…*; an offline device's own page has *Remove device…*. The confirmation names what still
uses each one (*Used by 2 automations: …*, dashboards, phone layouts, phone widgets) and offers *Remove
anyway*, which leaves those with a missing device. A removed device's name and settings given in Newman go
with it. Online devices can't be removed. If its connection reports the device again later, it simply comes
back as a new device.

### Frigate: new cameras and zones

Frigate restarts to apply a config change; Newman notices it coming back (its `<prefix>/available` going
`online` again) and re-reads Frigate's config — new cameras and zones appear, renamed ones, new streams and
tracked objects are updated, and ones Frigate no longer has are kept but unavailable. As a fallback (a missed
message) it also re-reads the config every 10 minutes and applies it when the cameras, zones or streams changed.

The connection's settings choose cameras and zones to **leave out** (*Leave out these cameras* / *zones*);
everything else, including cameras added to Frigate later, appears. Settings from older versions, which listed
the cameras to *include*, are converted once Frigate can be reached: left out = what Frigate has minus what
was ticked (all ticked = nothing left out). Until Frigate answers, the old list keeps applying.

Every object Frigate tracks is offered in automations - its own custom labels too (a `fedex` or `amazon`
model's labels show as *Fedex detected*, *Amazon detected*), not just the common ones (person, car, dog…).

### Camera video settings

Any device with live video or pictures, from any connection, has a **Video** tab on its page (admins):

- **Show in All Cameras** (on by default) — off leaves it out of the All Cameras grid on the dashboard,
  the wall tablet (and from the full-screen view's next / previous); it still has its page, tiles,
  automations and alerts.
- **Video from** — which connection supplies the device's live video and pictures: *<its connection> (this
  device)* by default (*(no video)* when it has no picture of its own), or another connection that has
  cameras. When both know where the camera is on the network (Frigate: the camera's go2rtc sources, through
  Frigate's own restream; Reolink: the device's address), the camera at the same address is used
  automatically — e.g. the Reolink doorbell "Front Door" at 10.1.1.56 offers *Frigate*, meaning Frigate's
  `doorbell` camera (also 10.1.1.56). Otherwise (or for cloud connections that don't know addresses) a
  *Camera in <connection>* choice lists that connection's cameras. Other cameras of the device's own
  connection, and devices whose video is just another device's (Frigate zones show their camera), are never
  offered; such a zone follows its camera, streams included. Its own events — button presses, motion —
  still come from its own connection; its tiles, page, full-screen view, wall alerts and the snapshots on
  its notifications use the other device's picture. A device that loses its video (or is removed) means
  the device's own picture is used again.
- **Tile stream** / **Full-screen stream** — for connections that offer several streams per camera
  (Frigate: the camera's go2rtc streams named `<camera>` or `<camera>_…`, plus any in its `live.streams`).
  *Automatic* (the default) is the connection's own choice; Frigate's is `<camera>_sub` for tiles when there
  is one, else `<camera>`. A picked stream that disappears counts as Automatic again.

Changes reach every open screen straight away (players reconnect).

### Home Connect (Bosch, Thermador, Siemens…)

Home Connect only talks to registered apps, so:

1. Sign up at **developer.home-connect.com** → *Applications* → *Register*.
   OAuth flow: **Device Flow**. *Home Connect User Account for Testing*: the email you use in the
   Home Connect app.
2. Paste the **Client ID** into Newman's Home Connect connection and save.
3. Newman shows a link and a code — open it, sign in to Home Connect, approve. Done; Newman keeps the
   token and renews it itself.

Notes: Home Connect allows ~1000 requests a day per app, so Newman reads everything once and then
follows Home Connect's live event stream. Dishwashers, ovens and washers drop off Home Connect when
switched off — Newman shows them as **Off**. *Start* only works after you press *Remote Start* on the
appliance (Home Connect's rule).

### LG ThinQ

Create a token at **connect-pat.lgthinq.com** (sign in with the LG account your ThinQ app uses →
*Add new token* → tick every permission) and paste it into Newman with your country. Appliances are
read every 30 s while one is running, every 2 min otherwise. Starting remotely needs *Remote
control* switched on at the appliance.

### Ring

*Connections → Add → Ring*: your Ring email and password, save; Ring texts (or emails) a code —
paste it under *Code from Ring* and save again. Newman keeps only Ring's token (renewed as Ring
requires) and forgets the password. The alarm, locks, sensors, sirens and keypads appear on their
own and change live; each lock / alarm / siren change carries who did it.

- **Who and how:** notifications say "Unlocked by Christopher" (the first name on the Ring
  account, or the keypad code's name — change it in the Ring app). With no one behind it, they say
  how, as the Ring app does: "Locked manually", "Locked by one-touch locking". If Ring's live update
  has no name, Newman looks in Ring's activity history for that exact change.

- The old **Ring activity** connection is now part of **Ring**: it's switched over automatically on
  the first start, keeping its sign-in, so there's nothing to set up again.
- **Moving off ring-mqtt:** both can run side by side. Meanwhile Newman also puts "who did it" on
  ring-mqtt's matching devices, so existing automations get names too. When ready, the Ring
  connection suggests which ring-mqtt device each of its devices replaces
  (`GET /api/integrations/<id>/replacements`), and `POST /api/entities/replace` moves automations,
  dashboards, device names and upkeep items to the new devices in one go. Then the ring-mqtt container
  can go.
- Ring won't arm while a door or window is open; tick *Bypass open doors and windows when arming*
  to have Newman bypass them, as the Ring app offers.

### Calendar server (CalDAV)

For calendars on your own server — GCloud's family calendars, or Nextcloud, Radicale, iCloud and other
CalDAV servers. Unlike an iCal link, it can also **change** them: the wall tablets add, edit and delete
events.

*Connections → Add → Calendar server (CalDAV)*:

- **Server address** — for GCloud, its share address with `/caldav/` (e.g. `https://share.example.com/caldav/`).
- **User name** and **Password** — for GCloud, a GCloud account made for Newman (e.g. `newman`) and an app
  password made for it (GCloud *Settings → Phones & app passwords*). Newman sees exactly the calendars that
  account can see: in GCloud, tick **On Newman** for a calendar (*Admin → Calendars*).

Every calendar shows as a calendar device, in its own colour, on the calendar tile, the Today page and in
automations ("event starts"), the same as iCal-link calendars. Calendars the account may only view show
without **Edit**, and with only those, the calendar screen has no **+ New event**.

**Changes show at once.** Put Newman's address (e.g. `10.1.1.2:8093`) in GCloud's *Admin → Calendars →
Newman address*, and an event added on a phone is on the wall within seconds. Any other server can do the
same by POSTing to `/api/public/calendars/changed`. Without it, Newman checks every 15 minutes (*Check
every*, under *More settings*).

**On the wall tablet:**

- On the calendar screen, tap an event for its card: when, where, notes, and a QR code for its link (scan
  it with your phone). **Edit** opens the form filled in; for a repeating event it asks *This event* or
  *All events*. Events of past and later months open and edit the same way.
- **+ New event** on the calendar screen adds an event, starting on the day picked there: what (needed),
  which calendar, all day, starts and ends (a big month calendar for dates; hour and minute wheels with AM / PM for times), and repeats (including
  *Custom…*: every few days, weeks, months or years, on chosen days, until a date or a number of times).
  Moving the start moves the end with it; an end before the start can't be saved. Save closes the form and
  shows *Added to <calendar>*. Notes and location are added on a phone or in GCloud.
- **Remind on the wall tablets** — tick calendars here and each event's reminders (the ones set on it in
  GCloud or on a phone) show as a card at the top of every wall tablet with a chime. **OK** on any tablet
  clears it on all of them.

The password is kept like other connections' keys (an app password: remove it in GCloud and Newman is cut
off). Times are written in the hub's time zone.

### Amazon Alexa (experimental)

Shows the timers set on your Echos on the wall tablets, next to Newman's own timers: the timer's name,
the countdown and which Echo it's on. At zero the card turns blue and says "Done · Ringing on <Echo>"
until Alexa is stopped (the Echo rings; the tablet stays quiet, and Alexa's timers have no ✕ — stop or
cancel them with Alexa). A done timer Amazon still lists is dropped after 15 minutes anyway.

*Connections → Add → Amazon Alexa*, then under *Settings*:

1. **Open Amazon sign-in** — Amazon's own sign-in page opens in a new tab. Sign in there (your password,
   and any code Amazon asks for, go to Amazon only).
2. When Amazon finishes, copy the address of the page it ends on (it starts
   `https://www.amazon.com/ap/maplanding?…`) from the browser's address bar, paste it into the box and
   press **Connect**.

Newman registers itself with your Amazon account as a device (it shows in Amazon's device list as
"<your name>'s Newman") and keeps only the refresh token Amazon gives it, in the connection's data
folder — no password, no authenticator key, no cookies. Each start, the cookies are made again from that
token. If Amazon cancels it (a password change, or the device removed from your Amazon account), the
connection says so and waits for you to sign in again — it doesn't keep trying. *Sign out of Amazon*
forgets the token and takes Newman off the Amazon account.

- **Show timers from** — untick an Echo to leave its timers off the wall tablets.
- **Your Amazon site** — where your Alexa account is (amazon.ca by default). When you sign in, Amazon
  says which site the account is on, and Newman uses that.
- **Check for timers every (seconds)** under *More settings* — 30 by default, 15 at the least.

This uses the unofficial connection the Alexa app itself uses (as Home Assistant's Alexa Media Player,
alexapy and ioBroker's alexa-cookie do): Amazon doesn't offer an official one for timers, and it can break
when Amazon changes things.

### DNS filters (AdGuard Home, Pi-hole, Technitium)

Each server is one **DNS filter** device: *Protecting* / *Off* / *Paused* (with the time it comes back on),
queries and blocks over the last day, % blocked, clients, blocking rules, version and whether an update
is out. Commands: turn on, turn off, **pause for N minutes** (the server turns itself back on). Events
for automations: *protection is turned off* and *comes back on*. Add as many servers as you have.

- **AdGuard Home** — its API has **no tokens or API keys**, only its web sign-in, so Newman has to keep
  a password (masked, never shown again). Don't give it yours: add a user just for Newman to
  `users:` in `AdGuardHome.yaml` (a bcrypt hash, e.g. `htpasswd -B -n newman`) and restart AdGuard Home.
- **Pi-hole v6** — *Settings → Web interface / API* (Expert mode) → *Configure app password*, and paste
  it. It's a separate key you can revoke, not your sign-in password. Newman signs in again by itself
  when Pi-hole's session runs out and signs out when the connection stops. **Pi-hole v5**: paste the
  API token (*Settings → API → Show API token*) instead.
- **Technitium DNS Server** — *Administration → Sessions → Create Token*, and paste the token
  (address like `http://192.168.1.2:5380`). Only the token is kept.

### Sprinklers

Hunter Hydrawise, Rain Bird and Rachio all bring in a **Sprinkler controller** (Idle / Watering /
Rain delay / Standby; *Stop watering*, *Rain delay* N days — 0 cancels, *Standby*, *Resume schedule*)
and one **Sprinkler zone** per zone (*Water for* N minutes, *Stop*; time left, last and next run;
*starts watering* / *finishes watering* for automations). Rename zones in Newman as you like.

- **Hydrawise:** app.hydrawise.com → *Account → Account Details → API Key → Generate*, paste it.
  Newman uses Hydrawise's key-based API (not the newer one, which needs your password kept). It
  doesn't report the rain sensor, and can't tell a rain delay from standby on its own: a suspension
  Newman set as a rain delay shows as **Rain delay**, any other full suspension as **Standby**.
- **Rain Bird:** the controller's IP address (give it a fixed one in the router) and the password
  set in the Rain Bird app. **Rain Bird has no tokens** — its local protocol encrypts every request
  with that password, so Newman has to keep it (masked, never shown back). Zones are named *Zone 1…*
  (Rain Bird keeps names only in its cloud). Rain Bird only knows "stop watering", so *Stop* on a zone
  stops whatever is running, and there's no standby over Wi-Fi (use a rain delay or the dial).
- **Rachio:** app.rach.io → your account → *Account settings → Get API key*, paste it. Rachio allows
  1,700 requests a day, so Newman checks what's watering every 3 minutes (slower with several
  controllers, hourly if Rachio says the day's allowance is nearly gone), plus once right after each
  command and when a run should end. Rain delay is up to 7 days.
  *Instant updates (optional):* if Rachio can reach `/api/public/webhooks/…` from the internet
  without a client certificate (let that path through the proxy), put Newman's public address under
  *More settings → Webhook address*; Newman registers itself with Rachio and then polls only every
  half hour. Behind a client-certificate proxy, leave it empty — polling is the default.

### Servers (Unraid, Linux computers, Proxmox)

Three connections watch the computers in the house. Each brings in a **server** (*Online* / *Needs a
look* / *Offline*; CPU %, load, memory %, CPU temperature, uptime, OS and version, pending updates; the
*Problem* property says what "needs a look" is about) and its **disks** (*OK* / *Needs a look* /
*Failed* / *Asleep*; used %, free, size, temperature, SMART, errors, power-on hours, SSD life left).
Events for automations: a server *goes offline* / *comes back online* / *gets too hot* / *CPU or memory
stays high* (for 10 minutes, by default); a disk *fails* / *shows a SMART warning* / *is almost full* /
*gets too hot*. The limits are under *More settings* on each connection. Something that was already
wrong when Newman started is shown but not alerted again.

- **Unraid** — Newman reads the official **Unraid API** (built into Unraid 7.x; the *Unraid Connect*
  plugin on 6.12). In Unraid: *Settings → Management Access → API Keys → Create*, role **viewer**, and
  paste the key with the address you open Unraid at (with its port, e.g. `http://192.168.1.10:8080`).
  Only the key is kept. The array is part of the server device — *Array* (started / stopped), *Parity
  check* (running / paused / not running) with its progress, the last check's result and errors, and
  containers running — with events *a parity check starts*, *a parity check finishes* (with the error
  count) and *the array stops*. Disk details come from Unraid's own table, which never wakes a sleeping
  disk; *Read SMART status* (off by default) may wake them.
- **Linux computer** (Debian, Ubuntu, Raspberry Pi OS…) — Linux can't be asked how it's doing without
  signing in, so Newman reads **Glances**, a small read-only monitoring agent many Home Assistant users
  already run (Glances 4 or 3 — Newman speaks both). On the machine, without Docker:

  ```bash
  sudo apt install pipx lm-sensors smartmontools
  sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install 'glances[web]'
  sudo tee /etc/systemd/system/glances.service >/dev/null <<'EOF'
  [Unit]
  Description=Glances (read by Newman)
  After=network-online.target
  [Service]
  ExecStart=/usr/local/bin/glances -w --disable-webui
  Restart=on-failure
  [Install]
  WantedBy=multi-user.target
  EOF
  sudo systemctl enable --now glances
  ```

  Or with Docker: `docker run -d --name glances --restart unless-stopped --network host --pid host
  -v /:/rootfs:ro -e GLANCES_OPT="-w --disable-webui" nicolargo/glances:latest-full` (the host's disks
  then show under `/rootfs`).
  Then add *Linux computer* with `http://<machine>:61208`. Glances has no API keys, so keep port 61208
  on your LAN. SMART per disk appears when Glances runs as root with `smartctl` installed. Already run
  Prometheus **node_exporter**? Pick it under *Reads it through* and use `http://<machine>:9100`
  (pending apt updates appear if its `apt` textfile collector is set up).
- **Proxmox VE** — *Datacenter → Permissions → API Tokens → Add* (e.g. user `newman@pve`, token
  `monitor`, privilege separation off or the roles below given to the token), role **PVEAuditor** on `/`
  to watch; add **PVEVMUser** (or just `VM.PowerMgmt`) to start, shut down and reboot guests. Paste the
  token ID (`newman@pve!monitor`) and secret with `https://<host>:8006`. Only the token is kept. Each node
  is a server (pending updates included), each storage and physical disk a disk (SMART health, SSD
  wear), and each VM / LXC container a **virtual machine** (*Running* / *Stopped* / *Paused*, CPU,
  memory; *Shut down* and *Force stop* ask for Face ID / fingerprint on a phone). Proxmox's API doesn't
  report temperatures — add the node as a *Linux computer* through Glances for those.

### Network checks

*Connections → Add → Network checks*, then one row per thing to watch:

- **Ping** — an address (`10.1.1.1`, `nas.local`). Uses unprivileged ICMP, which works in the
  container as is (no extra rights); where the system doesn't allow it, Newman checks that the host
  answers on a TCP port instead (a refused port still proves it's there).
- **TCP port** — `address:port` (`nas:445`, `10.1.1.2:8123`).
- **Web address** — the full link. *Expect* can be a status code (`200`, `401`…) or a word the page
  must contain; without it anything below 400 counts as up. **Web address (any certificate)** skips
  the certificate check (self-signed admin pages).

Each check is a *Network check* device: **online / offline**, with response time (ms), uptime over
the last 24 hours (%), the status code (web checks), the last change and the problem in plain words.
Its events — **goes offline** and **comes back online** — only fire after *N failures in a row*
(default 3, a connection setting), so one dropped ping doesn't count as an outage. Every check runs on
its own interval (default 60 s); the give-up time (default 5 s) is under *More settings*.

### Sun

*Connections → Add → Sun* — nothing to fill in. It's a one-click connection rather than built in, so
nobody gets a device they don't want; automations don't need it at all (see below). The Sun device is
**up** or **down**, with the next sunrise, sunset, dawn and dusk (civil twilight), and the sun's height
and compass direction; its events are **sunrise**, **sunset**, **dawn** and **dusk**. Everything is
worked out on the hub from the home's location — the Home place, else the Weather connection's spot;
until one is set the connection says so.

### TVs

Each TV is its own connection (*Connections → Add*, or one click from *Discovered*) and becomes one **TV**
device: on / standby / off, and — as far as the TV allows — volume, mute, input, app, playback and remote
keys. *Choose input*, *Open app* and *Press key* offer only what that TV lists. Turning a TV on that has
dropped off the network uses **Wake-on-LAN**: Newman learns the TV's MAC the first time it reaches it (or
type it in), and needs host networking (as in the compose file) for the broadcast.

| Brand | Setup | What you see on the TV | Limits |
|---|---|---|---|
| **Roku** (incl. TCL / Hisense / onn Roku TVs) | Nothing — found as e.g. “Basement TV (Hisense Roku TV)” | Nothing | Volume only steps up / down (no level). Recent Roku OS defaults *Control by mobile apps* to **Limited**: keys, power and inputs still work, but the app list, what's playing and opening apps are refused — set *Settings › System › Advanced system settings › Control by mobile apps › Network access* to **Permissive** for those. A Roku TV in standby wakes with its own PowerOn key. |
| **Samsung** (Tizen, 2016+) | Save; switch the TV on | “Allow Newman to connect?” — choose Allow. Newman keeps the key the TV hands back | Fully off = Wake-on-LAN, which needs *Power On with Mobile* (or *IP Remote*) on the TV. Inputs are the TV / HDMI keys. 2014–2015 sets (encrypted pairing) aren't supported. |
| **LG** (webOS) | Save; switch the TV on | “Allow this device to connect?” — Accept. Newman keeps the client key | On = Wake-on-LAN: turn on *Turn on via Wi-Fi* / *Mobile TV On* in the TV's settings. |
| **Android TV / Google TV** | Save; a 6-character code appears on the TV — type it into *Code shown on the TV* and save again | The pairing code | No app or input list in this protocol: common apps are offered (add more under *More settings*, `Name = package`), inputs via the Input key. Volume level is set by stepping. |
| **Apple TV** | Save; a 4-digit code appears — enter it and save; a second code follows (AirPlay, then Companion) — enter that too | Two codes, one after the other | No mute key. Sleep / wake through the Apple TV itself (no Wake-on-LAN needed). |
| **Hisense VIDAA** | Save; a 4-digit code appears — enter it and save | The pairing code | Built from community research (the hisense_tv / vidaa-control projects), untested on a real set. Many 2020+ sets also want the RemoteNOW app's TLS client certificate: Newman doesn't ship it — put the community-extracted certificate and key in the data folder and give their paths under *More settings*. Some 2023+ firmware uses per-device broker passwords and isn't supported yet. |

While a pairing code is waiting, the TV keeps its pairing session open for five minutes; if it times out,
save again for a new code. Pairing keys live in the connection's settings (Samsung, LG, Apple TV, VIDAA) or
its data folder (Android TV's certificate) — never a password.

### Music (Navidrome)

Music from your own [Navidrome](https://www.navidrome.org/) (or any Subsonic-compatible server) plays
**through the wall tablets' own speakers** — from the screen, by voice and from automations.

**Setting it up**: *Connections → Add → Navidrome*: the server address (on the NAS:
`http://10.1.1.2:4533` — use the address on your network rather than the public `music.garito.ca`), your
Navidrome username and password. Then open (or reload) each wall tablet: each one gets its own **music
player** device (“Hallway music”). Add a **Music** tile to a wall dashboard for search and albums.
A player belongs to the account the tablet is signed in with: once that account is no longer signed in on
any wall tablet, its player and queue are removed with the tablet's battery device (see
[Wall tablet battery](#wall-tablet-battery)); signing in again starts a fresh player.

**Only a token is kept.** Subsonic's token sign-in is `t = md5(password + salt)`: Newman uses the password
once to make a token with a random salt, checks it (`ping`), then keeps only the username, token and salt
(`integrations/<id>/token.json`, readable by the hub only) and drops the password from the settings. If
Navidrome stops accepting the token (you changed the password), the connection says so and asks for the
password once more. Accounts that sign in through LDAP can't use tokens — use a local Navidrome user. The
token never leaves the hub: the wall page gets the music through `api/music/stream/<id>` and the covers
through `api/music/cover/<id>`, which the hub fetches from Navidrome.

**How it plays**: the hub keeps each tablet's queue and tells that tablet (over its live connection) what
to play; the wall page plays it in an `<audio>` element and reports back position, paused and ended, so
*next* works and the device stays true (offline when the page hasn't reported for 3 minutes). Music goes
quiet (ducks) while Newman listens or speaks, while a timer or alarm rings and while a wall alert sounds, then comes
back. While music plays (and for a minute after it is paused), a small **now-playing chip** (cover, song · artist, a play / pause button) sits
with the status chips (in the top bar on other pages) — except on a page with a Music tile, which shows the
same. Tapping it opens the **player**: a card over the page with the cover, progress, previous / play /
next, volume, what's up next and **Lyrics** (tap outside or × to close; it closes itself after a minute
untouched). Synced lyrics follow the song with the current line lit; plain ones scroll.

**Lyrics** come from Navidrome first (lyrics in the files' tags or `.lrc` files next to them, through
OpenSubsonic, else the classic lyrics call), then — when the Navidrome connection's **Look up missing
lyrics online (LRCLIB)** setting is on (the default) — from [LRCLIB](https://lrclib.net), a free public
lyrics database. Only the song's artist, title, album and length are sent; nothing about you or your home.
Turn the setting off for Navidrome's lyrics only. Answers (including “none found”, for a few hours) are kept
per song under the connection's data folder.

**Music tile**: what's playing with its controls, a search box, a few recent albums (or playlists — the
tile's *Show* setting); *More* opens a full-screen picker (recent, artists A–Z, albums A–Z, playlists,
search). *Artists* lists every artist by letter as Navidrome groups them (“The” ignored), each with its
album count, and a letter strip down the side to jump (tap or slide) when the list is long; an artist
shows *Shuffle* (all their songs) and their albums, and *Back* returns to the same spot in the list.
An album — anywhere: the tile's covers, *Recent*, *Albums*, an artist's page, search — opens its page:
cover, name, artist, year, how many songs and how long, **Play** and **Shuffle**, then the numbered songs
with their lengths (the one playing is marked); tapping a song plays the album from that song on. A
playlist opens the same kind of page. *Back* returns to where you were, scrolled as you left it. In
search, an artist opens their page (as in *Artists*), an album or playlist its page, and a song plays it
and then the rest of its album. The keyboard's **Search** key shows every match — all the artists,
albums, songs and playlists — instead of the few shown while typing (from the tile's search box too).

**Voice** (see [what it understands](#voice-on-the-wall-tablet)): “play Jump by Van Halen”, “play Van
Halen”, “play the album 1984”, “play my road trip playlist”, “shuffle Queen”, “play some music”, “pause /
resume / stop the music”, “next song”, “previous song”, “volume up / down”, “set the volume to 40”,
“what's playing?”, “show the lyrics” (opens the player with the lyrics). Other ways in work too — “can you
play …”, “put on …”, “let's hear …”, “I want to hear …”, and “played / plays / playing …” (what speech-to-text
often makes of “play”) — as do “the song …” (a song, not an album or artist of that name), “something from
the album …” and “the band …”. A song is matched on its title *and* artist (“Remastered”, “(Live)” and a
leading “The” don't count). When Navidrome's own search finds nothing — speech-to-text mishears names
(“Better Roses” for “Bed of Roses”) — the library is matched by how the words look *and sound*: Navidrome
is asked for the whole phrase and for each longer word on its own, alongside every artist and album name
(kept 10 minutes), and each candidate is scored on its words, its letters and its consonant sounds (“bed of
roses” and “better roses” sound the same). Sure → it plays (“Playing Bed of Roses by Bon Jovi.”); fairly
sure → it asks “Did you mean Bed of Roses by Bon Jovi?” and a “yes” within two minutes plays it (“no” lets
it go); otherwise “I couldn't find better roses.” “Hey Newman, stop” with no timer ringing pauses the music.

**Automations**: the player's commands are ordinary device actions — *Play something* (words: a song
“Jump by Van Halen”, an artist, “the album 1984”, “my Dinner playlist”), *Shuffle*, *Pause*, *Stop*,
*Next song*, *Set volume* — e.g. “When someone rings the Front Door doorbell, pause Hallway music”. Its
song, artist, volume and playing / paused are properties to trigger on.

**For a while, then put back** (“play Bohemian Rhapsody on Kitchen music for 5 minutes”): before it
starts, Newman notes the player exactly — the whole queue, which song, how far into it (the tablet's last
report, every ~15 s, plus the time since), playing / paused / stopped, and the volume. When the time is up
the player goes back to all of that: playing music carries on from the same spot, paused music sits paused
at that spot, and nothing playing stops again. The run's log says what came back (“Put back: Van Halen –
Panama at 1:23, playing (volume 40)”). If someone changed the music in the meantime — put on something
else, paused or stopped it — it's left as they had it (“Left the music alone: it was changed during the 5
minutes”); skipping to the next song of the automation's music, or that music simply running out, still
counts as the automation's. The pending put-back is kept with the timer, so a restart of Newman during
those minutes still finishes it (straight away if the time ran out while it was down).

### Recipes (Forkast)

Your recipes from [Forkast](http://10.1.1.2:9700) open in **cook mode** on the wall tablet that heard you —
full screen, readable from across the kitchen, worked through by voice or touch.

**Setting it up**: *Connections → Add → Forkast*: the server address (on the NAS: `http://10.1.1.2:9700`),
your Forkast email and password. Newman signs in once with Forkast's login, keeps **only the sign-in token**
(`integrations/<id>/token.json`, readable by the hub only — Forkast set to `SESSION_DURATION=never` issues
tokens that never expire) and drops the password from the settings. If Forkast stops accepting the token (a
401: the password or Forkast's secret changed) the connection shows *Waiting for setup* and asks for the
password once more. Newman only reads: the recipe list (refreshed every 10 minutes, and again whenever a
spoken name isn't found — so a recipe added a minute ago is found), one recipe at a time, and its picture
(through `api/recipes/<id>/image`, so the tablet never talks to Forkast). The connection's one device, a
*Recipe book*, shows how many recipes there are.

**Cook mode** (one per wall tablet, on the hub; it closes itself after 3 hours untouched): the picture,
name and times; the **servings** with − / + (scaled amounts show *×2 · recipe makes 4*); the
**ingredients** with their amounts worked out for those servings — exact amounts (a kitchen fraction only when the amount is one, like ½ or ⅓; otherwise the exact decimal) for spoons, cups and
counts (1½ cups, ⅓ tsp, 2 large), exact grams and millilitres (52.5 g x 2 = 105 g), unit spellings made
consistent (“Tablespoon”, “tbsp”, “T” → tbsp); and the **steps**, the current one large and lit. Tap a step,
*Previous* / *Next*, or *Close*. While it's open the screen stays awake and the wall doesn't go back to its
home page. A **Recipes tile** (dashboard editor → add tile → *Recipes*) lists them with a search box; tap one
to open it.

**Voice** (on the tablet that heard it; Whisper's spellings are fine — “pan cakes”, “grandmas”):

| Say | Newman |
|---|---|
| “Pull up / open / show me the sesame chicken (recipe)” · “How do I make pancakes?” | opens it: “Here's Fluffy Pancakes: 9 ingredients and 5 steps.” — matched on the words of the name (“the”, “my”, “recipe” don't count; “sesame chicken” finds a long title that contains them). Two close matches (“the chicken recipe”): “I found two: Lemon Chicken Thighs or Honey Garlic Chicken Wings. Which one?” — answer “the honey one”, “the second one” |
| “Read me step 1” · “What's step 4?” · “Next (step)” · “Previous” / “Go back” · “Repeat that” · “What's the first / last step?” · “How many steps?” · “What step am I on?” | reads the step (temperatures and abbreviations said in full). Past the end: “That was the last step.” / “There are only 6 steps.” |
| “What are the ingredients?” · “How much flour?” · “How many eggs?” | the amounts for the current servings; an ingredient that's in there twice gets every amount (“1 cup of sugar, for the cake and a quarter of a cup of sugar, for the drizzle”); one without an amount is “to taste” / “as needed” |
| “Make it for 8” · “Scale it to 6 servings” · “Double it” · “Half” · “Back to the original amounts” | scales from the recipe's servings, on screen too |
| (a step with a time in it) | ends with an offer: “… Want a timer for 25 minutes?” — a range (“10 to 12 minutes”) offers the longer time and says so. **“Yes”** starts an ordinary timer named after the step (“step 4 timer”); “no thanks”, or anything else, and nothing starts |
| “Close the recipe” · “I'm done cooking” | closes cook mode |

“Next”, “go back” and “repeat” mean the recipe only while one is open (otherwise they're the music's);
step and ingredient questions with nothing open get “There's no recipe open. Ask me to pull one up first.”

## Matter and Thread

Matter is the cross-brand standard most new plugs, sensors and lights speak (the cheap IKEA ones —
GRILLPLATS plug, MYGGSPRAY motion sensor, KLIPPBOK leak sensor — included). Newman controls Matter
devices **directly on your network**, on its own Matter "fabric", without Home Assistant and without
the cloud. Tested so far against the real Matter server and a virtual Matter plug (matter.js); not yet
with physical devices.

### The pieces, in plain words

- **Matter controller** — something that controls a device: Apple Home, Google Home, and now Newman. Each
  controller has its own *fabric* (its own keys). A device can belong to several fabrics at once —
  Matter devices take **at least 5** — and each controller works on its own: that's *multi-admin*.
- **Commissioner** — the controller that adds a device. A brand-new Wi-Fi or Thread device is first set up
  over Bluetooth by a phone app (Apple Home, Google Home). Newman doesn't do Bluetooth: you add the device in
  Apple Home or Google Home as usual, then **share** it to Newman with a one-time code. Newman then adds it
  over the network onto its own fabric.
- **Thread** — a low-power mesh radio many battery sensors and plugs use instead of Wi-Fi. It's IPv6-only.
- **Thread border router** — the box that joins the Thread mesh to your home network: an **Apple TV 4K**
  (Wi-Fi + Ethernet models) or **HomePod**, a Google Nest hub, or an OpenThread border router. It announces
  a route to the Thread network (an `fd…::/64` prefix) on your LAN. Your Apple TV does this for every Thread
  device in the house — not only for Apple Home — so Newman reaches Thread devices *through* it.
- **The Matter server** — a small open-source container next to Newman that speaks Matter and holds
  Newman's fabric. Newman talks to it over a websocket on the same machine.

**If you leave iPhone (or Android) behind:** Newman's fabric is Newman's — nothing it does goes through
Apple's or Google's apps or clouds, so removing Apple Home from your phone, or the phone itself, doesn't
touch Newman's control. Thread devices still need a border router of *their* Thread network to be on: an
Apple TV keeps routing as long as it's plugged in and still in your home. If that border router goes away
for good, Thread devices need another border router on the same Thread network, or to be added again on a
new one. Wi-Fi and Ethernet devices don't need one at all.

### The host's IPv6 (for Thread devices)

Thread devices only have IPv6 addresses, so the machine running Newman and the Matter server needs:

- **IPv6 turned on**, on the same network (same LAN / VLAN) as the border router. LAN-only IPv6 is enough —
  the `fe80::` link-local address every interface gets once IPv6 is on. Your internet provider doesn't
  need to offer IPv6, and nothing is exposed to the internet.
- **`accept_ra = 2`** on that interface. The border router tells the LAN about the Thread network in a
  *router advertisement*. Linux ignores those on a machine that forwards packets, and Docker (and VMs,
  bridges) turn forwarding on — `2` means "listen to them anyway".
- **`accept_ra_rt_info_max_plen = 64`**. The Thread network arrives as a *route* inside the advertisement
  (a /64 route). Linux ignores advertised routes by default (`0`); `64` accepts them.

Use the interface your LAN address is on (Unraid: `br0`; Debian: e.g. `eno1`, `enp3s0`, or `br0` if you
made a bridge; TrueNAS: e.g. `enp3s0` or `br0`; Synology: `eth0`, or `ovs_eth0` with Virtual Machine Manager).
The examples below use `br0` — swap in yours.

**Check it** (the same on every system, after the steps for yours):

```bash
ip -6 -br addr show dev br0                            # IPv6 is on: at least an fe80::… address
cat /proc/sys/net/ipv6/conf/br0/disable_ipv6           # 0
cat /proc/sys/net/ipv6/conf/br0/accept_ra              # 2
cat /proc/sys/net/ipv6/conf/br0/accept_ra_rt_info_max_plen   # 64
ip -6 route | grep '^fd'                               # fd4a:ad96:cdb9::/64 via fe80::14e9:7353:c34e:52f5 dev br0 proto ra …
ip -6 neigh show fe80::14e9:7353:c34e:52f5             # the next hop's lladdr (MAC) …
```

The route needs a router advertisement to arrive (they come every few minutes), so give it a moment. The
last line's MAC should be your border router's (Apple TV: *Settings → General → About*, Ethernet or Wi-Fi
address). Newman's Matter connection shows the same thing in its status: *Thread network reachable*, or
*Thread devices can't be reached:* and what's missing (e.g. `br0 ignores router advertisements (accept_ra is
1 while forwarding is on; it needs to be 2)`).

#### Unraid

1. *Settings → Docker → Enable Docker: **No*** → Apply. Unraid only lets you change the network while
   Docker is stopped.
2. *Settings → Network Settings*: **Network protocol: IPv4 + IPv6**, **IPv6 address assignment: Automatic**
   → Apply.
3. *Settings → Docker → Enable Docker: **Yes*** → Apply.
4. Make the two settings stick across reboots: add them to `/boot/config/go`. Edit it with `nano
   /boot/config/go` (pasting a here-document or a long `echo` into the web terminal can mangle quotes and
   line breaks) and add at the end:

   ```bash
   # Matter / Thread: listen to router advertisements and the routes in them on br0
   sysctl -w net.ipv6.conf.br0.accept_ra=2
   sysctl -w net.ipv6.conf.br0.accept_ra_rt_info_max_plen=64
   ```

5. Run the same two `sysctl -w` lines once now (or reboot). **Changing Network Settings again re-creates
   `br0` and drops them** — run them again after any network change, then check.

#### Debian / Ubuntu

1. IPv6 is on by default. If your interface is set up in `/etc/network/interfaces`, make sure it isn't
   disabled (no `net.ipv6.conf.*.disable_ipv6 = 1` anywhere in `/etc/sysctl.conf` or `/etc/sysctl.d/`), and
   `iface br0 inet6 auto` is fine to add. NetworkManager / netplan: leave IPv6 on "automatic".
2. Create `/etc/sysctl.d/60-matter-thread.conf`:

   ```
   net.ipv6.conf.br0.accept_ra = 2
   net.ipv6.conf.br0.accept_ra_rt_info_max_plen = 64
   ```

3. `sudo sysctl --system`, then check. If the interface is a bridge made after boot (systemd-sysctl runs
   before it exists), also add to its `iface` block in `/etc/network/interfaces`:
   `post-up sysctl -w net.ipv6.conf.br0.accept_ra=2 net.ipv6.conf.br0.accept_ra_rt_info_max_plen=64`.

#### TrueNAS SCALE

1. *Network → Interfaces →* your interface *→ Edit*: tick **Autoconfigure IPv6** → Save, then **Test
   Changes** and **Save Changes**.
2. *System → Advanced Settings → Sysctl → Add*, twice (both *Enabled*):
   - Variable `net.ipv6.conf.br0.accept_ra`, Value `2`
   - Variable `net.ipv6.conf.br0.accept_ra_rt_info_max_plen`, Value `64`
3. They apply at once and at every boot. Check from *System → Shell*.

Run the Matter server as a Custom App (*Apps → Discover Apps → Custom App*) with **Host Network** on, or with
`docker compose` from the shell, as below.

#### Synology DSM

1. *Control Panel → Network → Network Interface →* your LAN *→ Edit → IPv6*: **IPv6 setup: Auto** → OK.
2. *Control Panel → Task Scheduler → Create → Triggered Task → User-defined script*: user **root**, event
   **Boot-up**; under *Task Settings → Run command*:

   ```bash
   sleep 30
   sysctl -w net.ipv6.conf.eth0.accept_ra=2
   sysctl -w net.ipv6.conf.eth0.accept_ra_rt_info_max_plen=64
   ```

3. Select the task → **Run** once, then check (*Control Panel → Terminal & SNMP* → enable SSH, `sudo -i`).

Some models run an older kernel built without advertised-route support: there
`/proc/sys/net/ipv6/conf/eth0/accept_ra_rt_info_max_plen` doesn't exist and the second `sysctl` fails. The
fallback is a static route in the same boot task — `ip -6 route add fd4a:ad96:cdb9::/64 via
fe80::14e9:7353:c34e:52f5 dev eth0`, with *your* prefix and border router address from another machine's
`ip -6 route` — but it silently breaks when the Thread network's prefix or the border router changes (a
reset, a new Apple TV, Apple handing the job to a HomePod), so check again after any such change.

### The Matter server container

Newman uses the **Open Home Foundation Matter Server** —
[matterjs-server](https://github.com/matter-js/matterjs-server), built on matter.js and maintained by the
matter.js team for the Open Home Foundation (Apache-2.0). It's the official successor of python-matter-server
(archived in June 2026; 8.1.2 was its last release) with the same websocket API. It's a standalone container:
no Home Assistant needed. It must use **host networking** (mDNS discovery and IPv6 to Thread devices).

```bash
mkdir -p /mnt/cache/appdata/newman/matter && chown -R 1000:1000 /mnt/cache/appdata/newman/matter
docker run -d --name newman-matter --restart unless-stopped --network host \
  -e LISTEN_ADDRESS=127.0.0.1 -e DEFAULT_FABRIC_LABEL=Newman -e PRIMARY_INTERFACE=br0 \
  -v /mnt/cache/appdata/newman/matter:/data \
  ghcr.io/matter-js/matterjs-server:stable
```

- `LISTEN_ADDRESS=127.0.0.1` — its websocket (port 5580) and its small web dashboard only answer on the
  server itself; Newman (host networking too) reaches it at `ws://127.0.0.1:5580/ws`. Matter traffic to the
  devices is unaffected.
- `DEFAULT_FABRIC_LABEL=Newman` — how Newman's fabric is named on devices (other apps may show it in a
  device's list of connected services).
- `PRIMARY_INTERFACE` — the LAN interface from above (it picks one itself if left out).
- It runs as user 1000, hence the `chown`. Its data folder **is Newman's fabric** — lose it and every
  Matter device has to be shared to Newman again. It sits inside Newman's data folder (`matter/`), so
  Newman's backups carry it ([Data and backups](#data-and-backups)).
- `docker-compose.yml` has it as the `matter` profile: `docker compose --profile matter up -d newman-matter`.

### Adding the connection

*Connections → Add → Matter*; the address is already `ws://127.0.0.1:5580/ws` → **Connect**. Its status
reads e.g. *no devices yet · Thread network reachable*.

### Adding a device

On the Matter connection's page, **Add a Matter device**, then paste a setup code and **Add**. Adding takes
a few seconds to a minute or two; the dialog shows what it's doing and for how long, and **Cancel adding**
stops waiting so you can try another code. It carries on if you close the dialog, and the next time you open
it, it shows how that went.

**From Apple Home** (iPhone / iPad): open the device → scroll down → **Settings** (the gear) → **Turn On
Pairing Mode** → **Copy Code**. Paste it into Newman within **about 15 minutes**; each code works once.

**From Google Home**: the device → **Settings** (gear) → **Linked Matter apps & services** → **Link apps &
services**; it shows a pairing code. Use it while Google Home still shows it.

**A device that's in no app yet**: only one that's already on your network (Ethernet, or Wi-Fi set up some
other way) can be added with its own code (the 11 digits under its QR code). Anything that needs Bluetooth
for its first setup — all Thread devices, most Wi-Fi ones — goes into Apple Home or Google Home first, then
is shared as above.

What it becomes is read from the device itself (its Matter device type and what it reports), whatever the
brand: a plug is a *switch* (with power W, energy kWh, voltage, current when it measures them), an occupancy
sensor *motion*, a water leak detector a *leak sensor* (wet / dry), a contact sensor a *door sensor*,
temperature and humidity sensors their readings, a bulb a *light*. Battery devices show their **battery
%**, what battery they take, and a *Battery low* health warning when the device says so (or ≤ 20 %) —
usable in automations like any other device.

**Buttons** (IKEA BILRESA and other Matter remotes) are one *remote* with an event per button, and per
button only what that button can tell apart (its Switch cluster's features): *“Button 1” is pressed*,
*double-pressed*, *long-pressed* (held down) and *let go after being long-pressed*. A button that reports its
release counts a press only once it's let go, so holding it down is never also a press; one that can't (older
buttons) still fires *pressed* the moment it goes down. With a light's **Brighten while held** / **Dim while
held** (see [Automations](#automations)), holding a button ramps a light up or down until it's let go.

**One device per physical device** for its readings: a temperature + humidity sensor is one *temperature
sensor* that also reports humidity and battery; a motion sensor that measures light is one *motion* sensor
with a light level (lx); a leak or door sensor keeps its battery and any temperature on itself. Each device
behind a bridge is its own device. Things you switch or move stay separate — each outlet of a power strip is
its own *switch*, with its own power reading. Every reading can start automations (*humidity goes above
60 %*) and shows on the device page and in *Readings* tiles. Devices an older Newman split into one per
reading are merged when Newman starts: the merged device keeps the main reading's id, and automations,
dashboards and a name given to the old reading devices move to it.

### Removing a device

The **Paired** tab lists what's on Newman's fabric → **Remove**. It leaves Newman's fabric only: Apple Home,
Google Home and any other app that controls it keep it. The other way round, removing it in Apple Home
normally takes only Apple's fabric off it and Newman keeps working; if it shows *not answering* in Newman
afterwards, the other app reset it — share it to Newman again.

### When it doesn't work

- ***No device waiting to be added answered … pairing window may have closed*** — the code expired (Apple
  Home's lasts about 15 minutes) or was already used. Turn on pairing mode again for a new code.
- ***The device turned that code down*** — a mistyped or old code. Make a new one.
- ***Thread devices can't be reached: this computer has no IPv6 route to a Thread network …*** — the host
  settings above; the status says which part is missing. Run the *check it* block.
- **A device shows *not answering*** — no power, out of range, or (Thread) its border router is off. The
  route can also vanish after a network change on the host (Unraid: re-run the two `sysctl` lines).
- ***As many controllers as it can*** — the device already has its 5 (or more) fabrics in use: remove it
  from an app you no longer use.
- **Battery sensors answer late** — they sleep between reports; that's normal.
- A firewall on the host must not time out UDP "connections" quickly (sleepy devices answer minutes later).

**Example — charge a wall tablet only between 20 and 80 %** with a Matter plug (see
[Wall tablet battery](#wall-tablet-battery)):

- **When** *Kitchen tablet's battery goes below 20%* → **Then** turn on *GRILLPLATS plug*.
- **When** *Kitchen tablet's battery goes above 80%* → **Then** turn off *GRILLPLATS plug*.

## Companion containers

Some connections need a small helper next to Newman. None of these are part of Newman's image.

### Docker socket proxy

Needed for the **Docker** connection. Newman *could* read Docker's socket directly
(`-v /var/run/docker.sock:/var/run/docker.sock`, address `unix:///var/run/docker.sock`), but that
socket gives full control of the host — anything that can talk to it can start any container or read
any file. Since Newman is reachable from the internet, put a proxy in between that only lets through
what Newman needs:

```bash
docker run -d --name docker-socket-proxy --restart unless-stopped \
  -p 127.0.0.1:2375:2375 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e CONTAINERS=1 -e EVENTS=1 -e PING=1 -e VERSION=1 \
  -e POST=1 -e ALLOW_START=1 -e ALLOW_STOP=1 -e ALLOW_RESTARTS=1 \
  lscr.io/linuxserver/socket-proxy:latest
```

- `-p 127.0.0.1:2375:2375` keeps it on the server itself (Newman uses host networking, so it reaches
  it at `http://127.0.0.1:2375`). Nothing else on your network can use it.
- `CONTAINERS=1 EVENTS=1` — list containers, their stats and live events. Images, exec, volumes,
  networks and everything else stay blocked.
- `ALLOW_START=1 ALLOW_STOP=1 ALLOW_RESTARTS=1` — let Newman **start / stop / restart** containers.
  `POST=1` allows changes in general (it also permits other container changes, e.g. creating one, through
  the proxy, which still only listens on `127.0.0.1`); set it to `0` to allow just those three. All four
  left out = a watch-only connection (Newman then says the connection is read-only if you try).
- On Unraid it's also available from Community Apps as **socket-proxy**.
- `docker-compose.yml` has it (no profile: `docker compose up -d` starts it with Newman; delete it there
  if you don't want the Docker connection).

Then in Newman: *Connections → Add → Docker*, address `http://127.0.0.1:2375`. Every container
appears, including ones created later; tick any under *Leave out these containers* to keep them out.
A container shows up once it has existed for two minutes. Throwaway ones never do, however long they
run: anything started with `docker run --rm`, or with the label `newman.ignore=true` (add it to build or
test containers you remove yourself). One deleted from Docker (gone from `docker ps -a`, not just stopped) is removed from Newman after
10 minutes - straight away when the connection starts - unless an automation, dashboard, phone layout
or widget still uses it: then it stays, unavailable, shown as *Removed*. While Docker can't be reached
nothing is removed.

### Speech: Whisper and Piper

For **voice on the wall tablets** ([Voice on the wall tablet](#voice-on-the-wall-tablet)): the standard
Wyoming speech containers (the same ones Home Assistant uses), CPU only. Newman talks to them over
the Wyoming protocol; they only listen on the server itself.

```bash
docker run -d --name newman-whisper --restart unless-stopped -p 127.0.0.1:10300:10300 \
  -v /mnt/cache/appdata/newman/whisper:/data \
  -v /mnt/cache/appdata/newman/voice:/hint:ro -e WYO_WHISPER_INITIAL_PROMPT_FILE=/hint/whisper_prompt.txt \
  rhasspy/wyoming-whisper --model small.en --language en --compute-type int8 --beam-size 1 --cpu-threads 4

docker run -d --name newman-piper --restart unless-stopped -p 127.0.0.1:10200:10200 --memory 512m \
  -v /mnt/cache/appdata/newman/piper:/data \
  rhasspy/wyoming-piper --voice en_US-lessac-medium
```

- Whisper `small.en` (what the NAS runs: ~550 MB of RAM in use) hears names best, about 1.5 s for a short
  command on a 6-core CPU. `base.en` (~150 MB download, ~350 MB of RAM) is faster — about 0.6 s — but
  mishears more. Piper downloads its voice (~60 MB) the first time; the voices offered in a tablet's
  settings are fetched when first used.
- Then for Newman: `VOICE_STT=127.0.0.1:10300`, `VOICE_TTS=127.0.0.1:10200` (and `VOICE_NAME` for
  the default reply voice). Either one unset turns that half off.
- The `/hint` mount and `WYO_WHISPER_INITIAL_PROMPT_FILE` give Whisper the home's names to expect — the
  list Newman writes to its data folder's `voice/whisper_prompt.txt` ([The Whisper hint](#voice-on-the-wall-tablet)).
  Whisper reads it only when it starts, so Newman restarts the container itself when the list changes —
  through the [Docker connection](#docker-socket-proxy), which needs restarts allowed on the socket proxy
  (`POST=1`, or just `ALLOW_RESTARTS=1`). No Docker connection: `docker restart newman-whisper` after big changes. The
  file must exist before Whisper starts (Newman writes it as soon as it runs;
  Whisper retries until then).
- `docker-compose.yml` has both as the `voice` profile: `docker compose --profile voice up -d`.
- **Kokoro** (optional, a more natural voice — what the NAS uses): run
  [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) (CPU image, ~0.5-1.5 s per reply on a
  6-core CPU, ~2-3 GB of RAM) and set `VOICE_TTS_KIND=openai`, `VOICE_TTS_URL=http://127.0.0.1:8880`,
  `VOICE_NAME=af_heart`. Any OpenAI-compatible speech service (`POST /v1/audio/speech`) works the same way.
  ```bash
  docker run -d --name newman-kokoro --restart unless-stopped -p 127.0.0.1:8880:8880 --memory 3g --cpus 6 \
    ghcr.io/remsky/kokoro-fastapi-cpu:latest
  ```
  (or `docker compose --profile kokoro up -d`).
  The tablets' voice choices are then Kokoro's English voices (American / British, women and men).
  Keep `VOICE_TTS` set too: when Kokoro fails or is down, Piper speaks the reply instead (logged once).
- The third voice companion, **`newman-wakeword-trainer`**, retrains “Hey Newman” with your household's
  voices when asked from the dashboard — see [Teaching and training “Hey Newman”](#teaching-and-training-hey-newman).

### Matter server

For the **Matter** connection: the Open Home Foundation's
[matterjs-server](https://github.com/matter-js/matterjs-server), with host networking, as container
`newman-matter` — how to run it, and the host's IPv6 settings Thread devices need, are in
[Matter and Thread](#the-matter-server-container).

### Zigbee (Zigbee2MQTT with an SLZB-06)

For **Zigbee devices**: [Zigbee2MQTT](https://www.zigbee2mqtt.io) runs the Zigbee network through a
network coordinator — an SMLIGHT **SLZB-06** on Ethernet (PoE or USB power) — and puts every paired
device on MQTT with Home Assistant-style discovery. Newman's **MQTT** connection reads that, so Zigbee
devices show up like any other MQTT device: nothing Zigbee-specific in Newman.

1. **The SLZB-06:** plug it into the network, open its web page (its address is on your router's list
   of clients) and give it a fixed IP address. Leave it in its default *Zigbee coordinator* mode, LAN
   connection; Zigbee2MQTT reaches it on port 6638.
2. **`docker-compose.yml`**, service `newman-zigbee` — every setting is there; change the `CHANGE` lines:
   - `ZIGBEE2MQTT_CONFIG_SERIAL_PORT` — `tcp://<the SLZB-06's address>:6638`.
   - `ZIGBEE2MQTT_CONFIG_SERIAL_ADAPTER` — `zstack` for the SLZB-06 (and -06P7: Texas Instruments
     CC2652P chip); `ember` for the SLZB-06M / -06MG24 (Silicon Labs chip).
   - `ZIGBEE2MQTT_CONFIG_MQTT_SERVER` — your MQTT broker, the one Newman's MQTT connection uses.
   - Home Assistant discovery is on, on the `homeassistant` prefix — the MQTT connection's default
     *Discovery prefix*; change both together or neither.
3. **Start:** `docker compose --profile zigbee up -d newman-zigbee`. Its data goes in
   `/mnt/cache/appdata/newman/zigbee/` (the network key it makes on first start, the paired devices, the
   coordinator's own backup) — inside Newman's data folder, so Newman's backups carry it.
4. **In Newman:** *Connections → Add → MQTT devices* with the same broker (if it isn't there yet).
5. **Pairing:** open Zigbee2MQTT's own page at `http://<server>:8094`, **Permit join (All)**, then put the
   device in pairing mode (usually a long press or a few power cycles — its manual says). It appears in
   Zigbee2MQTT within a minute, then in Newman. Rename it in Newman as usual. Joining is closed whenever
   Zigbee2MQTT starts and closes again by itself after a few minutes.

Mains-powered Zigbee devices (plugs, bulbs) also extend the network; put a few between the SLZB-06 and
far-away battery sensors.

### Mosquitto

For **MQTT devices** (ring-mqtt, Zigbee2MQTT…): any MQTT broker. Newman publishes `online` on
`homeassistant/status` when it connects, so bridges like ring-mqtt re-send their devices exactly as
they do for Home Assistant. It isn't in `docker-compose.yml` — most homes already run one (the NAS: an
`eclipse-mosquitto` container with port 1883 published). Add it in the dashboard: *Connections → Add →
MQTT devices*.

Frigate, likewise, runs on its own; Newman only needs its address (*Connections → Add → Frigate*).

## Where people are

Each person's phone can share its location with the hub (Household → **Map** on the dashboard, the
People tab in the phone app). Only the newest fix is kept, on the hub only — no history. Inside
Home a person shows **Home**; inside a named place, its name (**School**); otherwise how far they are
from home. Phones report on their own about every 5 minutes (and when they arrive somewhere or leave).

**Battery first: there's no live following.** Tapping someone (on the wall's Family map, Household →
Map or the phone app) asks for **one** fresh fix: the hub sends their phone a single locate push and
the phone answers once. That's at most **once every 2 minutes per person**, whoever asks (a second tap
meanwhile just waits for the answer). **iPhones answer even when the Newman app has been closed
(swiped away)**: with sharing on and location access *Always*, the app's location push extension is
woken and sends the fix; Android phones (and iPhones without *Always*) get the silent push. Nothing is
shown to the person beyond the phone's own location indicator.

**Photos and colours.** Everyone has a colour (a distinct one from a palette is picked for anyone who
hasn't chosen, and kept) and can add a photo: Household → People → the person (admins, for anyone) or
your Profile → *On the family map*. Any JPEG, PNG or WebP up to 3 MB is cropped to its centre square
and kept as a small 256 px JPEG in the data folder (`people/`, included in backups). Maps and lists show
the photo, or the first initial on their colour.

- **Sharing is set per account** (Household → Accounts → *Location*): **Their choice** (the default —
  they switch it on in their app, off until they do; the household sees them), **Required** (always on,
  they can't turn it off — the app shows "Required by Chris"; *Seen by* picks the people and wall
  tablets who can see them, everyone by default) or **Off** (not shared, no switch in the app).
  Switching it off deletes what was kept.
- **Places** (Household → Places, admins): Home is built in — it starts at the weather connection's spot
  with a 150 m radius; move it or resize it there. Add others by clicking the map. The radius slider
  runs smoothly from 25 m to 5 km (finer at the small end) and the circle follows as you drag. When
  someone is inside more than one place, the smallest wins.
- **Who sees it**: household members (admin and family accounts). Guests never see anyone's location,
  and guests aren't on People at all.
- **Wall tablets** see people only with their account's **Can see where people are** (Household →
  Accounts → the tablet; on by default). With it on, a tablet sees everyone sharing by *their choice*,
  and *Required* people whose *Seen by* is everyone or ticks that tablet. Turned off, its Family tile
  shows everyone as *Not sharing*. Tablets never share a location themselves.
- **The map** (Household → Map and Places, and the wall's People map) is **Apple Maps** when the hub
  has a MapKit JS key, and OpenFreeMap otherwise (or whenever Apple's map can't load — blocked, offline,
  key refused — the page switches to it by itself). The wall's Family tile has a **Map look** setting:
  *Light* (the default) or *Dark*.
  - **Apple Maps** (MapKit JS): brighter, with easy-to-read street names. The browser loads MapKit JS
    and the map imagery straight from Apple (`cdn.apple-mapkit.com` and Apple's map servers); the hub
    only hands signed-in maps a short-lived token (`GET /api/map/token`, signed with your key, 30
    minutes, renewed as it runs out — the key itself never leaves the hub). Apple sees which map areas
    are viewed (as with any map service), not where anyone is: people's locations and your places are
    drawn on top in the browser and never sent to Apple. The token isn't tied to one web address
    (no `origin` claim), because the hub is reached at several (its LAN address and port, and the
    public name behind the proxy) — so anyone who gets hold of a token could use it for up to 30
    minutes; only signed-in accounts get one.
    **Setting it up** (Apple Developer account → Certificates, Identifiers & Profiles):
    1. *Identifiers* → **+** → *Maps IDs* → e.g. `maps.ca.nimaste.newman`.
    2. *Keys* → a key with **MapKit JS** enabled, set to that Maps ID (an existing key can have it
       added — the APNs push key works, so one `.p8` does both). Download the `.p8` and note its Key ID.
    3. Give the hub `MAPKIT_KEY_ID` (the Key ID). The team is `MAPKIT_TEAM_ID`, which defaults to
       `APNS_TEAM_ID`. The key file is `MAPKIT_KEY_PATH`; when it isn't set it is the APNs key file
       (`APNS_KEY_PATH`) if `MAPKIT_KEY_ID` is the same as `APNS_KEY_ID`, else
       `/app/keys/mapkit.p8` (the mounted keys folder, `/mnt/cache/appdata/newman/keys/`).
    4. Restart. `GET /api/map/token` now answers `"provider": "apple"`. If the key can't be read the
       hub logs one line (“Apple Maps off: can't use the MapKit key …”) and the maps use OpenFreeMap.
  - **OpenFreeMap** (no key needed): a vector map drawn in the browser with MapLibre — OpenFreeMap's
    *Positron* style when light and its dark-blue *Fiord* when dark, from
    [OpenFreeMap](https://openfreemap.org) (OpenStreetMap data; free, no key). The hub fetches the
    styles, tiles, fonts and icons from `tiles.openfreemap.org` — the only server it asks — names itself
    when it does, and keeps copies in `map-tiles/` in the data folder (tiles, fonts and icons 30 days;
    styles 6 hours, refreshed in the background while the old copy is served). The browser or wall
    tablet only ever talks to the hub, so this map works the same away from home behind the
    client-certificate proxy, and an area already seen still shows when the internet is down.
## Automations

Built in the dashboard as **When → What happens → Only if**, read back as a plain sentence
("When Side Door stays unlocked for 10 minutes, lock Side Door and notify Chris").

- **When** — any mix of: a time, or sunrise / sunset (± minutes: “30 min before sunset”); a device changing state (optionally *staying* that way for a while);
  a device event (doorbell pressed, person detected, vacuum finished, container crashed, printer
  needs attention…); any reading a device reports going above / below / to a value (optionally for a
  while). *Any* of them, or *all* of them within a time window.
- **What happens** — commands on any number of devices, optionally *for* a while (then put back —
  devices that keep more than an on/off state, like a music player's queue and spot in a song, are put
  back exactly, unless someone changed them meanwhile; see [Music](#music-navidrome));
  **Brighten while held** / **Dim while held** on any dimmable light, for a button's *long-pressed* event
  (a Matter button's, or a Lutron Pico's: *“Raise” is long-pressed* once it's been down half a second, and
  *“Raise” is pressed* only when it's let go sooner, so a hold is never also a press):
  the light ramps until that button is let go (at most 10 s). Zigbee2MQTT and Matter lights ramp smoothly by
  themselves, like their own dimmer remotes (one "start moving" when it's held, "stop" when it's let go,
  bottom to top in about 3.5 s); other lights go in steps of 10 % every 0.4 s, each fading into the next
  where the light can fade. An off light comes on low, and dimming stops at the lowest level, never off;
  notifications with action buttons and, for camera events, a snapshot; showing it on wall tablets;
  and a **live status** on phones (below) of any devices — until they're no longer in a state, for a
  while, or until something else happens (a device changes, an event, a time).
- **Only if** (optional) — device states and readings (any / all / none), between times, on certain days, only when
  it's dark (sunset to sunrise, each ± minutes, across midnight) or only during daylight, and
  between dates — every year (“December 1 to January 6” wraps over New Year; February 29 counts as
  the 28th in other years) or just once in a given year. Dates use the hub's own date.

**Don't repeat for** - after it runs, an automation ignores new triggers for that many minutes (logged as a
skipped run). Left on the default it doesn't wait, except one started by a camera detection, which waits
the camera default (Settings › Cameras, 1 minute to start). Brighten/dim while held ramp in the time set in
Settings › Lights (3.5 s to start), a tap steps 10 % (5, 10 or 20), and a Pico counts as held after 0.5 s.

**Who can change them** - admins always; anyone else only when an admin switched on *Make & change automations* on
their account (Household › Accounts); wall tablets never. Everyone can still see them - on the web without
New automation, switches or Save.

A new blank automation starts with one empty *Do* row and no notification (*+ Add something that
happens* → *Send a notification* adds one); *Only if* starts empty, and a device row left without a
device is left out when it's saved. Recipes and *Create automation* from a device keep their own starts.

**Follow-ups, one per change.** A notification waiting on devices tells you about each one as it's
taken care of: with Side Door and Laundry both unlocked, locking Laundry (the *Lock* button, by hand, or
at the keypad) gets you “Chris locked Laundry.” right then, and Side Door later gets its own. Doors
locked together — *Lock all*, or two within about 10 seconds — share one (“Chris locked Side Door and
Laundry.”). A door that's been locked is always told, even if the alert is then dismissed or runs out;
reminders only name the doors still open.

**Reminders.** *If nobody does, remind again after* 15 minutes, 30 minutes, 1 hour or 2 hours, *up to*
1–5 times (3 unless you pick otherwise; automations made before the choice existed remind 3 times).
Snoozing replaces the reminders still to come: when the snooze ends you get one reminder, then the usual
ones start over (up to the same number again). A run never outlives 12 hours.

**One notification per person per change.** When a device change answers a notification that's still
open with you — the nightly lock check is waiting for Laundry, and you tap *Lock* (or lock it by hand, or
at the keypad) — that conversation's follow-up (“Chris locked Laundry.”) is the one you get. Any other
automation notifying about that same change (say, a *tell me whenever a lock changes* one) still runs —
its actions, its Activity entry, its run log (“Not sent to Chris: covered by Nightly lock check's
follow-up”) — but doesn't push to the people the follow-up is for; anyone it notifies who wasn't in that
conversation still gets it. Two ordinary automations about the same change, with no conversation open,
both notify as usual. Wall-tablet alerts are unaffected (follow-ups only go to phones).

Every reading a device reports is available — not just the ones Newman has a friendly name for.

## Remotes and their Controls

A remote (a Pico, IKEA's BILRESA, any button its connection reports) has **Controls** on its page:
the lights it controls and what each press does. It's a setting of the remote, not automations, so
the Automations list stays for real automations.

* **Lights** - tick the lights (or switches) it controls; the remote's own room is listed first.
* **When it turns them on** - at a set brightness, or at the brightness they had last.
* **Hold to brighten and dim** - holding the top (on) button brightens, the bottom (off) one dims,
  smoothly, until you let go.
* **Buttons** - every press the remote has (a tap and, where it can be held, a hold). Each does the
  standard thing for its button unless you pick something else: **Control a device** (any device and
  anything it can do), **Run an automation**, or **Nothing**.

The standard thing comes from the button's job, which the connection reports: on, off, raise (a bit
brighter; held, brighten), lower (a bit dimmer; held, dim). Lutron Picos' On / Off / Raise / Lower
and the paddle Pico's top and bottom, and Matter buttons tagged Top + On / Bottom + Off (the BILRESA),
are known; a button without a job (a Pico's ★, numbered buttons) does nothing until you give it one.
A remote can also control no lights and give each button its own job.

Remotes whose button automations only switched and dimmed one set of lights (on at a level, off,
brighten and dim while held) had those automations turned into their Controls once, when this came in;
Activity says which. The phone apps show a remote's Controls; they're changed on the dashboard.

## Upkeep and Today

**Upkeep** (dashboard → Upkeep) holds things that come due again and again: the furnace filter,
softener salt, an oil change, smoke-alarm tests. Each item repeats every N days / weeks / months
and/or after N hours a device has run (the furnace and AC, counted from the thermostat's own
heating / cooling). Optional: who it belongs to, how many days ahead it shows as *coming up*,
a heads-up the evening before, and whether the wall tablet asks *who did it*. Items live in the
Upkeep connection's data folder (so backups carry them). Each item is also a device (*Upkeep item*:
up to date / coming up / due today / overdue), so automations can notify when one comes due.

**Today** is one briefing the hub works out for the wall tile, the phone and (later) voice:
- **To do** — upkeep items due or overdue, devices needing a hand (offline, low battery, no power,
  jammed) and appliance needs ("rinse aid low"), most urgent first. **Done** on the wall asks *who*
  for everyone's items (skip allowed); an item with an owner credits them; a phone always records
  its person. **Undo** takes back a mis-tap.
- **On today** — today's calendar events.
- **Heads-up** — weather warnings, rain above a threshold (default 50%), and from the evening on
  (default 17:00) tomorrow's events from calendars marked *"the evening before"* (e.g. garbage day)
  and upkeep items so marked. Calendar links from Google and most others don't include your own
  reminders, so this is set per calendar in Newman.

## Dashboards and the wall tablet

Dashboards are grids of tiles (devices, groups, cameras, the round thermostat dial, weather,
calendar agenda, alerts, people…), with per-tile text size and per-dashboard display names. Each account
picks its dashboard; the wall tablet (`/wall`) shows it full screen with tabs you swipe between,
live camera video, and the weather and calendar details a tap away. A camera tile has a button for
every one of its cameras, in a row that scrolls sideways (*All cameras* stays at its end); swiping the
picture left or right goes to the next or previous camera, and dots under it show which one it is.
Lists on the wall (a Devices list, Needs attention, Climate glance rooms, To do) never leave anything
out: when the tile is short the list scrolls, fading at the bottom while there's more below.

**Sizes, looks and taps.** The wall's grid is 24 columns by 16 rows, so tiles size and move in
half-steps (dashboards made before it was this fine were doubled once, automatically: they look the
same). Every tile also has, in the dashboard editor:
- **Look** (tiles that show a list: Climate glance rooms, Devices, Readings) — *Auto* (small cards when
  the tile has room, a list when it's narrow - about a third of the tablet), *Tiles* or *List*. A list
  is a row each with the full name; a devices list has a small icon, the state and a compact button.
- **When tapped** — *Do what this tile does* (the default), *Open a page*, *Open a screen* (Cameras,
  Calendar, Weather, To do), *Run an automation* (as if run by hand: its Only if still applies),
  *Control a device* (a device and something it does without a value, like Lock), or *Nothing*. The
  tile's own buttons, dial and the like keep working; it's tapping the tile itself.
- **When held** — the same choices (holding the tile for about ¾ of a second); *Nothing* by default.

**Calendar.** The *Coming up* tile shows as many whole events as fit (never a cut-off one, no "+N more");
tapping anywhere on it opens the calendar screen with the tile's calendars. That screen is a month,
Sunday to Saturday (days of the months either side dimmed; tap one to go there): **‹ ›** or a swipe go a
month back or on — past months too — **Today** comes back, and the chosen day's events are on the right
(*This day* / *Everything* coming up). **+ New event** (calendars Newman may change) adds one on the chosen
day. Months outside what a calendar keeps ready (yesterday to *days ahead*) are fetched from the calendar
when shown (`GET /api/calendars/{id}/events`, see API.md) — a calendar server, or an iCal link's feed.

**Camera replays.** Until an alert's clip can play, its snapshot shows under a veil with *Loading the
clip…*; a player that fails tries again by itself (3 tries) and only then says *This clip couldn't be
played.* with **Try again**.

**Family map tile** (tile type `people`). A row of name chips — photo or coloured initial, first name,
and where they are with how old that is (*Home · 4 min*, *School · 9 min*, *6.4 km · 1 min*); anyone
not sharing (or not visible to this tablet) is dimmed as *Not sharing* — above a map (the tile's *Map
look*: Light, the default, or Dark; Apple Maps or OpenFreeMap as above). Optionally only some people
(*Only these people*; none ticked = everyone).
- **The home view** fits everyone within about 50 km of Home (never less than ~3 km across). Anyone
  farther away isn't squeezed onto it: their chip says how far (*353 km*) with an arrow pointing their
  way from home, and a label sits at that edge of the map. **Everyone** fits all of them.
- **People at the same place** (the same named place, or within ~150 m) share one bubble of stacked
  avatars tagged *Home · everyone* / *Home · 3*. Tap it for a card of who's there, *since* when (their
  latest update), with a refresh button each; tap outside to close it. Single pins carry a first-name tag.
- **Tap a name chip** (or a pin, or a row in the card): it asks for that person's fresh location (the
  chip shows *Updating…* until it arrives — see *Battery first* above) and flies the map to them, about
  1.5 km across. **Back to home view** returns, and so does a minute untouched; while it's flown away
  the tablet counts as in use (it doesn't go back to its home page). Household → Map works the same way
  (bubbles, the home area, *Show everyone*). "Show Dante on the map" by voice still opens him full screen.

**Home page and mornings.** Each dashboard has a *home page* — where the wall tablet rests — set in
the dashboard editor (side panel → **Home page**): the usual home page, optionally another page in
the mornings (Mon–Fri and Sat–Sun each with their own from / to, by the tablet's own clock), and
*go back to the home page after N minutes untouched* (default 2; 0 = never). When a morning starts
or ends the tablet moves to the new home once nobody has touched it for 30 seconds; a full-screen
camera, an open sheet or a person's map counts as in use. Every page stays in the tab bar either way, and wall alerts
still appear over whatever is showing.

**The top bar's page tabs.** Each tab is an icon; the dashboard editor's side panel → **Tab names**
picks *Icons, current tab named* (the default: only the page you're on shows its name) or *Names when
they fit* (every name while the whole row fits, otherwise the same as the default). The clock and date
keep their room first, then the music chip and dim button; the tabs sit at the centre of the bar when
there's space on both sides, otherwise right after the clock, and with more pages than fit even as icons
the row scrolls sideways (the current tab kept in view) - none is ever cut off.

**Themes.** Each dashboard has a look, picked in the dashboard editor's side panel → **Theme**:
- **Midnight** (the default): the deep blue night look the wall has always had.
- **Glass**: light, iOS-style frosted panels over a soft colour wallpaper, rounder corners, Apple's
  system fonts on Apple devices (Nunito elsewhere). Sheets, the music player and picker, cook mode,
  the voice card and alerts all turn light with it; a full-screen camera stays dark.
- **Christmas**: soft winter blue with frosted tiles and red and green throughout - a thin pine
  garland with red berries and little gold lights along the very top of the screen, a sprig of holly
  in a free corner of each tile, cranberry-red warm and pine-green cool colours (room bars go green to
  red), the current tab in pine green with a red edge, a red top edge and bow on the Climate glance
  tile, a candy-cane thermostat arc, a pine-green play button with a red rim - plus a snowdrift along
  the bottom, glowing string lights along the top of camera pictures, gentle falling snow (at most 60 flakes; paused while the
  screen is dimmed or hidden; none with *reduce motion*), and a greeting in a script font with three
  characters beside the clock - Newman's own drawings of Santa, a red-nosed reindeer and an elf.
  Options: **Falling snow** on / off · **Greeting** (default *Merry Christmas*; empty = none) ·
  **Characters**: *Built-in*, *Your pictures* (up to three PNG / WebP pictures with a see-through
  background, 2 MB each, kept on the hub and removable) or *Off* · **Characters go**: *By the clock*,
  *Soft, behind the tiles* (big and blurred) or *Nowhere*. The greeting and characters only get the
  width the page tabs leave over; when the bar is too full for them (lots of tabs, a tablet held
  upright) the characters go alone, and whatever didn't fit appears big and soft on the dimmed screen
  instead.
- **Christmas season**: switched on, Christmas replaces the chosen theme by itself every year between
  two dates (December 1 to January 6 unless changed; over New Year is fine). The wall checks at its
  local midnight, and switches straight away when the dashboard is saved.
The fonts are part of Newman's web app, so the wall needs no internet for them.

**Climate glance tile** (tile type `climate`, look only). *Climate* with the thermostat's mode and
what it's doing (*HEAT · IDLE*), an arc showing where it's set with the room's temperature as the big
number, *Set to 22.5°*, humidity and the outside (from a weather place, if chosen); then a box per
chosen room - as boxes (name, temperature, humidity when the device reports it, and a cool-to-warm bar,
two or more to a row) or, with its Look a list (or Auto on a narrow tile), a row each: a cool-to-warm
dot, the full name, humidity and temperature (every room; they scroll when the tile is short). Numbers are exactly what the
devices report. Settings: **Thermostat** (anything set to a temperature), **Rooms** (devices that report
a room temperature), **Outside** (a weather place, optional), plus Look, When tapped and When held. A
tap opens the thermostat's own dial unless When tapped says otherwise (an older "Opens page" became
When tapped: Open a page). It is for the wall (the phone apps don't draw it yet).

**Updates reach open screens by themselves.** A wall tablet (or a browser tab) keeps running the page
it loaded until something reloads it - for days on a tablet. So every page compares its own web build
with the one the hub serves (`webBuild` in the live socket's hello and in `GET /api/hello`) whenever
it reconnects, when it comes back into view and every 10 minutes, and reloads itself when they differ:
- **the wall** once nobody is using it: not while Newman listens or speaks (cook mode's step reading
  included) or a timer / alert sounds, not while music plays (paused is fine), not within a minute of a
  touch, and not while a sheet or dialog opened or touched in the last 10 minutes is up. It stays
  dimmed if it was, and keeps its page;
- **other pages** on the next page change, or when the tab comes back after being hidden - never while
  a field has focus or an editor has unsaved changes.

If a reload didn't bring the new build (the hub mid-update), it waits 1 minute, then 5, then 30 between
tries. The browser console says what it's doing (`[newman] web build: …`); `newmanBuild()` in DevTools
shows this page's build, the hub's and why it's waiting. Nothing is needed in the Android / iOS wall
apps (their web views revalidate the page as browsers do; `index.html` is sent `no-cache`).

**The Today page** (layout B: *Today · Overview · Security · Lights · Climate*, Today in the mornings
6–10 / weekends 7–12, then Overview): a greeting with today in one line ("3 things to do · 3 events
today · rain from 3 PM"), **To do** grouped as overdue / today / coming up with Done right there —
as many as fit, the rest behind *See all* — the 31-day agenda and the weather. New dashboards start
with it; for an existing one, **Add a Today page** in the editor makes it (using the dashboard's own
weather and calendar choices) and turns the mornings on.

**Whose dashboard.** The Dashboards page shows the *Wall & browser* dashboards as a table — name, pages,
**Default for** (*You*, *Kitchen*) and **Shown on** — so two dashboards both called "Wall tablet" (yours and the
wall tablet account's) are easy to tell apart; one that is nobody's default names its owner under it. *Shown on*
counts the devices showing it right now (the names on hover or click): only sign-ins in use, seen in the last three
days. A wall tablet that signs in again (a cleared browser, a reinstalled app) leaves its old sign-in behind until
it expires a year later; those don't count, and expired sign-ins are deleted.

### Phone layouts

*Older phone apps only.* Current apps show [the phone's Home](#the-phones-home), set up on the phone
itself, and the web no longer has a phone layout editor. Older apps keep getting the layout last saved
(a household default, or a person's own) through the API ([API.md](API.md#phone-layouts)) until they update.

### Wall tablet battery

A tablet that lives on a wall mount and charges 24/7 sits at 100 % all day, and that is what makes
its battery swell (sometimes enough to push the screen out). The Newman Wall app reports the
tablet's battery to the hub — on start, every 5 minutes, and straight away when charging changes or
the level moves 5 % — and each wall tablet account becomes a device (**Wall tablets** connection,
kind *Wall tablet*: charging / on battery, with battery %, battery temperature °C and voltage V as
readings). With its screen off it shows **Screen off**, not offline - and so does its music player: the
Android wall keeps checking in every 15 minutes in the background, even in Android's deep sleep (Doze),
and says when the next check-in is due. Where a check-in should get through - charging or plugged in,
or allowed to ignore battery optimisation - it's shown offline 35 minutes after the last one, or 20
minutes after an announced one that didn't come. On battery otherwise, Android's deep sleep may hold
check-ins back for hours: that's expected, so it stays *Screen off* (no alert) and is only offline after
8 hours without a word. The wall rarely gets that far: **its screen never stays off**. It holds the
screen on (dimmed after a while, with the date and time), and whatever turns it off - the power button,
Fire OS's timeout over another app (Alexa, say), its charging or Show Mode screen, the charger being
plugged in or out - the wall turns it straight back on into its dimmed screen and comes to the front,
trying again every 30 seconds while it stays off. Its 15-minute check-ins do the same, and start the
wall again if Android closed it. This needs Android's *Display over other apps* for Newman Wall (the
wall's settings have a button for it); without it the tablet's health says so. If a Fire tablet still drops off overnight, its wall settings have
an optional **Let it report while asleep** (section *Battery reports while asleep*: lets the app ignore
battery optimisation; or once over USB:
`adb shell dumpsys deviceidle whitelist +ca.nimaste.newman.wall`); an iPad can't, so
it says goodbye as it sleeps and shows *Screen off* for up to 12 hours (its battery counts as unknown
after 15 minutes). Screen on, it's shown offline after 15 minutes without a report.

The device belongs to the account the tablet is signed in with, and lasts as long as that account has a
wall-tablet sign-in Newman still accepts — however long the tablet is asleep or quiet. When the account
has none left (signed out, the sign-in ended from the devices list, expired, or the account disabled or
removed) the device and its last report are removed, along with its music player; one that expired goes
at the hourly check (and at start). It's removed outright, so smart charging switches nothing and sends
no alert; the account's smart charging settings stay, ready for when it signs in on a tablet again.
Automations and dashboards that used it say the device no longer exists.

**Smart charging** keeps it between 20 and 80 % without any automations: turn it on under
**Household → Accounts → the tablet → Charging**. The hub switches the tablet's charger plug on when
the battery drops below *Start charging below* (20 % unless you change it) and off when it rises above
*Stop charging above* (80 %) — only when the battery **crosses** one of those levels (and once when
you turn it on, or after a restart, if it's already outside them), so switching the plug yourself is
respected until the next crossing. Each switch is in the tablet's Activity ("Turned Kitchen plug on:
battery 19%"). The charger is **learned by watching**: when one plug (any device that switches on and
off) goes on or off and within 10 s exactly that one tablet starts or stops charging, that's its
charger ("Learned: charges from Kitchen plug") — move the tablet to another plug and it relearns.
Newman never switches plugs to find out; you can also pick the plug yourself. Admins get one push
(at most hourly per tablet, again once it has charged) when a tablet with smart charging is below
10 % and not charging, or its charger isn't responding when it should turn on.
A tablet going quiet - offline, asleep past its check-in, or its battery unknown - isn't a fault: the
hub can't see its battery any more, so it just turns the charger on, without telling anyone ("Turned
Kitchen plug on: Kitchen tablet went quiet" in its Activity); its next report takes it from there (off
again above *Stop charging above*). Only when a tablet has been silent for 3 hours with its charger on
(or no charger set) do the admins get one push for that silence: "Kitchen tablet has been off for 3
hours even with its charger on - it may be dead or frozen" (an iPad, which is silent all night anyway:
once it's offline, after 12 hours).
Changing the charger acts at once with the new one (from the last level reported).
If the hub turns the charger on and the tablet hasn't started charging a minute later, it asks the
admins once ("Kitchen tablet didn't start charging from Hall plug. Which plug is it on?") with up to
three likely plugs as buttons (same room first) and *None of these*; the same question shows in the
tablet's Charging settings. Picking one makes it the charger and turns it on; the learning then
confirms it ("Confirmed: charges from Kitchen plug").
The battery temperature is the early warning: a battery that runs warm while charging is the one
that swells — add *When Kitchen tablet's battery temperature goes above 40 °C → notify me* (and
turn the plug off).
## Voice on the wall tablet

Say **“Hey Newman”** to a wall tablet, then what you want. Everything stays in the house: the wake
word is heard on the tablet itself, the words are turned into text and the reply into speech on the
server, and nothing is recorded or kept (Activity logs the words heard and what Newman did).

```
tablet (Newman Wall app)                     hub (Newman)                    speech containers
 "Hey Newman"  ── openWakeWord, on-device
 chime, records until you stop (≤ 8 s)
 page ── POST /api/voice/utterance ──────▶  Whisper (wyoming-whisper) ──▶  text
                                             intent → action → reply
 plays the reply ◀── JSON + WAV ────────────  Piper (wyoming-piper) ◀────  reply text
```

**What it understands** — worked out from your devices' names, rooms (areas), each wall dashboard's
“show as” names and what each kind of device can do (`kinds.py`), so new devices just work:

| Say | Newman |
|---|---|
| “Turn off the kitchen lights” · “Lock the side door” · “Close the garage” | does it: “Okay, the Kitchen Lights are off.” (plural / “all” = every matching device) |
| “Set the main floor to 21” · “Set the thermostat to heat” · “Dim the island to 30 percent” | sets the number / mode (checks the device's range) |
| “Unlock the side door” · “Open the garage” · “Disarm the alarm” | **“For safety, do that from the screen.”** — anything `kinds.py` marks sensitive, unless that tablet's *Allow unlocking, opening and disarming by voice* is on |
| “Is the garage open?” · “Is the side door locked?” · “Is anything unlocked?” | answers from the current state |
| “What's the temperature outside / in the basement?” · “How much time is left on the dishwasher?” | reads the matching reading (“outside” = the weather) |
| “What's the weather?” · “How's the vacuum?” | the device's state and key readings |
| “Where's Dante?” · “Where is everyone?” | home / at a place / how far — only people this tablet may see (its *Can see where people are* and their sharing) |
| “Run Good Night” · “Trigger the leaving automation” · “Start the movie night routine” | runs that automation now, like its *Run* button (“start” / “activate” need *automation* / *routine* said, or its whole name — “start the vacuum” stays the vacuum's). One whose actions unlock, open or disarm needs the tablet's *Allow unlocking, opening and disarming by voice* |
| “What's on today?” | today's events, to-dos and heads-ups (the Today briefing) |
| “What time is it?” · “What's the date?” · “What day is it?” | “It's 4:52 PM.” / “It's Tuesday, October 6th.” — in the hub's time zone (`TZ`) |
| “Show the front porch camera” · “Show the cameras” · “Show Lights” · “Show Dante on the map” · “Show the weather” | full-screen camera, all cameras, a page of the tablet's dashboard, the person's live map, weather / calendar / to-do |
| “Set a timer for 10 minutes” · “Set a 2 minute timer” · “Set a pasta timer for 8 minutes” · “…for an hour and a half” · “How much time is left (on the pasta timer)?” · “Cancel the timer” · “What timers do I have?” | timers kept on the hub per tablet (they survive the wall reloading), shown as countdown cards; one that ends rings (see [Alarms and timers](#alarms-and-timers)) |
| “Extend the timer by 4 minutes” · “Add 4 minutes to the pasta timer” · “Give it 2 more minutes” | “Okay, the pasta timer has 9 minutes left.” (a ringing one too: it runs again) |
| “Take 2 minutes off the timer” · “Knock a minute off the eggs” | “Okay, the eggs timer has 3 minutes left.” (nothing left: it rings) |
| “Pause the timer” · “Resume the pasta timer” | “Okay, paused with 6 minutes left.” / “Okay, the pasta timer is running again.” |
| “Restart the timer” · “Change the timer to 15 minutes” · “Make the pasta timer 12 minutes” | back to its full time (“…is back to 8 minutes.”) / a new full time, from now (“…is now 12 minutes.”) |
| “Cancel all timers” | “Okay, both timers are cancelled.” |
| (several timers, none named) | “Which one: pasta or eggs?” — answer without “Hey Newman”: “eggs”, “the second one”, “the 10 minute one”, “both”. “The 10 minute timer” and “the second timer” (the order they were set) work as names anywhere |
| “Set an alarm for 7” · “…for 6:30 tomorrow” · “Wake me up at 7 AM” · “Set a gym alarm for 5:45” | “Okay, alarm set for 7:00 AM tomorrow.” — no AM / PM and no day: the next 7 coming; with a day (“tomorrow” is always the next calendar day) or a repeat it asks “6:30 in the morning or the evening?” |
| “…set an alarm for 6:30 on weekdays” · “…every Saturday at 8” · “…every day at 7” | “Okay, alarm set for 6:30 AM on weekdays.” |
| “What alarms do I have?” | “You have 2 alarms: the gym alarm at 5:45 AM tomorrow, and 6:30 AM on weekdays.” and opens Alarms & timers |
| “Cancel my 7 o'clock alarm” · “Cancel the gym alarm” · “Turn off my weekday alarm” · “Turn on my Saturday alarm” | cancels (deletes) / turns it off or on; a repeating one first asks “Just tomorrow, or every time?” (the real next day: “Just Monday…”) — “just tomorrow” skips that day only |
| “Change my 7 o'clock alarm to 7:30” · “Skip my weekday alarm tomorrow” | the new time (the same half of the day unless said) / that one day skipped |
| “Snooze” · “Snooze for 5 minutes” · “Another minute” (while something rings) | snoozes the alarm (the tablet's *Snooze for*) / gives the timer another minute |
| “Stop” | stops what's ringing (timers and alarms) and anything being said (with nothing ringing: pauses the music) |
| “Play Jump by Van Halen” · “Play the album 1984” · “Shuffle my road trip playlist” · “Pause the music” · “Next song” · “Set the volume to 40” · “What's playing?” | music from Navidrome on this tablet's own speaker — see [Music](#music-navidrome) |
| “Pull up the pancakes” · “Next step” · “How much flour?” · “Double it” · “Close the recipe” | cook mode with your Forkast recipes — see [Recipes](#recipes-forkast) |

Something it can't place gets an honest answer (“Sorry, I didn't catch a device called bedroom lamp.”,
“Which one: Main Floor or Basement?”). “Alarm” on its own is the security alarm (“Disarm the alarm”, “Turn
off the alarm”); it's one of Newman's alarms when said with set / wake me / snooze, a time, a name, a day or
a repeat, “my … alarm” or “alarms”, or while one rings.

**Questions back** — when Newman asks something (“Which one: pasta or eggs?”, “Just tomorrow, or every
time?”, “6:30 in the morning or the evening?”, “Did you mean Massimo?”), the wall listens for the answer
straight away — a chime and *Listening…*, no wake word — for about 6 seconds; nobody answering lets it go
quietly. “Hey Newman, eggs” within a minute works too; anything that isn't an answer is taken as a new request.

**Misheard names** — Whisper sometimes spells a name the way it sounds (“back yard”, “good nite”, “ma
seemo”). When a name isn't found as heard, Newman takes a second look against every name it knows —
devices (with your renames and the dashboards' “show as” names), rooms, people, automations, recipes,
dashboard pages, the music library — comparing letters, words and how they sound
(`server/newman/voice/names.py`, the same matching the music search uses). Only names of the kind the
request needs count (a person for “where is…”, a lockable device for “lock…”, an automation for “run…”),
a phrase said exactly as some other name is left alone, and the corrected request must then make sense:

- sure → done as corrected (“turn on the back yard lights” → the Backyard's lights, “run good nite” → Good
  Night); the reply's `action.corrected` says which name was meant;
- fairly sure → it asks: “Did you mean Massimo?” — “yes” within a minute does it, “no” lets it go;
- otherwise the honest “Sorry, I didn't catch …” as before.

The names are kept by the hub and updated as things change (a device added, removed or renamed, an
automation saved), so a rename is understood straight away.

**The Whisper hint** — Whisper hears names better when it's told which to expect. Newman keeps a short
list — “Newman.”, the household, rooms, devices (most used first), automations, then the most played
artists, about 200 Whisper tokens at most — in the data folder's `voice/whisper_prompt.txt`, rewritten a
few seconds after the names change (`GET /api/voice/hint` shows it). The Whisper container reads it when
it starts (`WYO_WHISPER_INITIAL_PROMPT_FILE`, see [Companion containers → Speech](#speech-whisper-and-piper));
wyoming-whisper takes no per-request prompt (its `transcribe` request only carries the language), hence
the file.

*Keeping Whisper current* — Newman restarts the Whisper container (`VOICE_STT_CONTAINER`, default
`newman-whisper`) through the Docker connection when the file is newer than the container's last start, and
only when it's safe: the list unchanged for 5 minutes (a burst of renames restarts once), no request being
heard and none in the last minute — preferably none for 10 minutes, unless the change has waited 2 hours —
never within 30 minutes of the container's last start (by Newman or anyone), and never a container that's
stopped. It then waits for Whisper to answer again (usually 10–20 s) and logs it (“newman-whisper restarted
with the new hint, ready in 14 s”). Something said meanwhile waits for Whisper and is heard then; if it
isn't back within 20 s the tablet hears “One moment, I'm just getting ready…” instead of an error.
`GET /api/voice/hint` → `whisper` shows where it stands. Needs a Docker connection that has the container
with restarts allowed on its socket proxy (`POST=1`, or just `ALLOW_RESTARTS=1`); `VOICE_STT_RESTART=0`
turns it off.

Why not per request: wyoming-faster-whisper can fetch names on every utterance, but only by logging in to a
Home Assistant install's websocket API (`--hass-token`) and reading its exposure, state, entity, device,
area and floor registries — Newman would have to pose as Home Assistant and squeeze people, automations and
artists into Home Assistant's entity tiers. A restart when nobody is talking keeps Newman's own list.

**Per tablet** (Household → Accounts → the tablet → *Voice*): listen on/off, the *wake word*, wake
sensitivity (low / normal / high), reply volume, the reply voice, and *Allow unlocking, opening and
disarming by voice* (off). Below them: *Teach “Hey Newman”* and *Train “Hey Newman”* (see
[Teaching and training “Hey Newman”](#teaching-and-training-hey-newman)).

**Changing the wake word**: pick it in the tablet's *Voice* settings → *Wake word*. The tablet switches
within seconds (the page tells the wall app the new model's version; it downloads the model from the
hub) — no app restart, no new app. Offered: “Hey Newman” plus openWakeWord's pre-trained “Hey Jarvis”,
“Hey Mycroft” and “Hey Rhasspy” (Newman downloads those into the data folder's `voice/` at startup
when they're missing; “Alexa” is left out so the house's real Alexa devices don't answer too). Any other
openWakeWord model works too: drop `<name>.onnx` into `voice/` with an optional `<name>.json`
(`{"phrase": "Hey Computer", "threshold": 0.5}`; the phrase defaults to the file name, “hey_computer” →
“Hey Computer”). A tablet whose chosen model is removed goes back to the default (`VOICE_WAKEWORD`).
The wall's hints (“say Hey Jarvis and try again”) use the chosen phrase.

**On the tablet** the app asks for the microphone once (a device-owner kiosk grants it itself) and
listens only while the wall is on screen. It lets go of the microphone while it answers. Android lets
the tablet's own assistant keep its hotword while another app records with the voice-recognition
source, so “Hey Google” should keep working — see [`android/README.md`](android/README.md#voice) for
what to check on a new tablet. The iPad wall app listens the same way: the same models through ONNX
Runtime's iOS package, the same endpointing and the same page events, while the wall is on screen
(iPadOS doesn't allow listening in the background) — see [`ios/README.md`](ios/README.md).

**When it can't listen** the wall says so instead of silently not hearing: the microphone not allowed →
a banner *Hey Newman can't hear you. Newman Wall isn't allowed to use the microphone.* with **Allow the
microphone** (Newman Wall's own screen in Android's / the iPad's settings); speech not available on the
hub → a plain line (*Hey Newman isn't available: the speech containers aren't running* / *speech isn't set
up on the hub* / *the wake word is still downloading*). It starts listening by itself as soon as it can:
the hub checks speech every 10 s while it's down (60 s while it's up) and tells the tablets, the wall asks
again every 30 s while it can't listen, and the app checks the microphone whenever it comes back to the front.

### Alarms and timers

Newman's own **alarms** and **timers** on a wall tablet, by voice (above) or by touch, kept on the hub and
belonging to the tablet they were set on — they still go off after the wall app, the tablet or the hub
restarts (`GET /api/voice/alarms`, `/api/voice/timers`, see [API.md](API.md#voice)).

- **On the wall**: the next alarm in the coming 24 hours shows as a card beside the timer cards
  (*Alarm · weekdays · 6:30 AM · Tomorrow*); a snoozed alarm's card counts down to its next ring (✕ calls
  the snooze off). Timer cards have ⏸ / ▶ beside the ✕, and a paused one says *Paused*. Tapping an alarm
  card opens *Alarms & timers*; tapping a timer card opens that timer.
- **Alarms & timers** (tap the big clock at the top of the wall, an alarm card, or ask “what alarms do I
  have”): **Timers** first — each with its time left, *of* its full time, ⏸ / ▶ and **Open** — and
  **+ New timer**; then **Alarms** — on / off and **Edit** — and **+ New alarm** (*No timers* / *No alarms*
  when there are none). Holding the clock still shows who is signed in.
- **A timer** (a card or Open): the big countdown; −1 min, +1 min, +5 min; Cancel, Restart, Pause / Resume.
- **+ New timer**: 1, 3, 5, 10, 15, 30 min or 1 hour start one at once; or hours and minutes and a name,
  then Start.
- **New / Edit alarm**: the time (hour, minutes in fives plus the alarm's own minute, AM / PM), the days
  it repeats (none = once, the next time it comes), a name; Delete, Cancel, Save.
- **Ringing**: the wall wakes fully bright with a full-screen *ALARM 6:30* (or *PASTA TIMER · Done*) —
  **Snooze 9 min** (the tablet's *Snooze for*) or **+1 min** for a timer, and **Stop** — or “Hey Newman,
  stop” / “snooze” / “another minute”. It rings through the wall app on the tablet's **alarm volume**
  (Android's alarm channel, the *Alarms & timers* slider), not the media volume, with the sound chosen in
  the app's settings; music and Newman's speech go quiet meanwhile. A timer says “Your pasta timer is
  done” once first. It rings until stopped, up to the app's *Stop ringing after*; a one-time alarm then
  turns off, a repeating one waits for its next day, and a timer goes. Two at once: the first to ring
  shows, Stop shows the next; “Hey Newman, stop” stops them all. The ringing screen is over everything,
  wall alerts too.
- **Keep the alarm volume at full** (Android wall app: settings › Sound, a switch under the *Alarms &
  timers* slider, off by default): on a Fire tablet, Alexa turns the alarm volume down each time one of her
  own alarms or timers rings, so they come out quiet. With the switch on, the wall app puts the alarm
  volume straight back to full whenever anything lowers it, for as long as the app is running. It never
  touches the other volumes and never turns anything down. While it's on, the *Alarms & timers* slider
  stays at full; turning it off leaves the volume where it is. Not on the iPad: iOS doesn't let apps change
  the volume, and there's no Alexa app ringing alarms there.
- **If the hub can't be reached** at an alarm's time, the wall page rings it itself 30 s later, and the
  Android app rings it a minute late even without the page (its own *Stop* / *Snooze* over the wall) —
  the page keeps it the coming alarms for that. In a plain browser the page beeps instead.
- **Daylight saving**: an alarm's time is the hub's clock time (`TZ`), so 7:00 stays 7:00 when the clocks
  change; a time the spring change skips rings when the clock shows the hour after (2:30 → 3:30).
- **The iPad wall app** rings with Newman's own sounds (*Beeps*, *Chimes* — the default — *Bell*,
  *Rising*; iOS lets no app use the iPad's alarm tones) at the iPad's volume, and only while the wall is in
  front (iOS doesn't let an app ring by itself in the background).

### Setting voice up

1. Start the two speech containers (see [Companion containers → Speech](#speech-whisper-and-piper)) and
   set `VOICE_STT=127.0.0.1:10300` and `VOICE_TTS=127.0.0.1:10200` for Newman.
2. Put the wake-word model in the data folder's `voice/` (e.g. `/mnt/cache/appdata/newman/voice/`):
   `hey_newman.onnx`, openWakeWord's `melspectrogram.onnx` and `embedding_model.onnx`, and optionally
   `hey_newman.json` (`{"threshold": 0.5}`). The wall app downloads them from the hub (and again
   whenever they change), so a retrained model needs no new app. The pre-trained wake words
   (`hey_jarvis.onnx`, `hey_mycroft.onnx`, `hey_rhasspy.onnx` + `.json`) are fetched by Newman itself
   from openWakeWord's v0.5.1 release (`…/releases/download/v0.5.1/hey_jarvis_v0.1.onnx` etc.).
3. Restart Newman. Each wall tablet then listens (its *Voice* settings decide).

Measured on the NAS (i5-10400, CPU only): Whisper `base.en` (int8, beam 1) transcribes a 2–3 s command
in about 0.6 s (≈ 1 s while the CPU is busy), Piper `en_US-lessac-medium` speaks a reply in 0.1–0.2 s;
end of speech → reply playing ≈ 0.8 s (the tablet waits for 0.8 s of quiet) + 0.8–1.2 s ≈ **1.6–2 s**.
`tiny.en` is faster (0.35 s) but mishears (“main board”, “positive timer”); `small.en` takes 1.5 s.
The NAS now runs `small.en` with Kokoro (0.5–1.5 s a reply), trading some speed for hearing names right.

### Teaching and training “Hey Newman”

The first “Hey Newman” was trained on computer voices only, so it misses real people more than it
should (it catches about 4 in 10 computer-voice tries, with ~2.5 false wake-ups per hour of everyday
sound). Newman can retrain it with **your household's own voices**, recorded on the wall tablet's own
microphone — no Claude, no cloud, nothing leaves the NAS.

**1. Teach (record each person, ~3 minutes each).**

1. On the dashboard: *Household → Accounts →* the wall tablet *→ Voice → Teach “Hey Newman”*.
2. Pick who's recording (anyone in *Household → People*, or *Someone else…* and type a name) and press
   **Start on this tablet**.
3. At the tablet a full-screen card appears: “Say “Hey Newman” (1 of 30)”. Say it, pause, say it again
   — each one is picked up automatically (the bar shows it hears you). Follow the tips: the first ten
   in your normal voice where you usually stand, the next ten from across the room / quieter / louder
   / faster, the last ten with the TV or music on. **Redo last** throws away the last one, **Skip
   ahead** goes to the next step, **Done** stops.
4. Then “Now just talk normally for 30 seconds”: press **Start** and read the paragraph on screen. This
   teaches it what *isn't* “Hey Newman”.
5. Repeat for everyone in the house (kids too — their voices are the hardest for the computer-voice
   model). The tablet doesn't listen for the wake word while teaching, and stays on the card (it
   counts as in use).

A clip that's silent, too short or too long is turned away with a reason on the card (“That was too
quiet - move closer or speak up”). The list under *Teach* shows how many each person has recorded,
with **Delete** to remove all of someone's recordings.

**2. Train (about half an hour, runs on its own).** Under *Train “Hey Newman”* press **Train** (at least 20
“Hey Newman” recordings are needed). The `newman-wakeword-trainer` container (below) picks it up and
shows its progress there; you can close the page. It keeps the 20k computer-voice clips it already
has and adds yours: every recording is copied ~60 times with room echo, background noise and
different loudness, the talking is cut into short pieces as “not the wake word”, and about one in five
of each person's recordings is held back to test the result.

**3. Compare and use it.** When it's done the section shows the new version next to the one in use:

| | In use now | New version |
|---|---|---|
| Catches your voices | e.g. 30% | e.g. 90% |
| False wake-ups per hour | 2.5 | 0.6 |

“Catches your voices” is measured on the held-back recordings it never trained on (per person below
the table); false wake-ups on ~11 hours of everyday sound (TV, music, talking). **Use it** installs it
on every tablet within seconds (they download the new model; no app update). **Keep current** throws
the new version away. **Undo** puts the previous version back (and Undo again re-installs the newer
one). Train again any time after recording more.

**Where things live** (the data folder, e.g. `/mnt/cache/appdata/newman/`):

- `voice/teach/<person>/positive/*.wav` — the “Hey Newman” recordings (16 kHz WAV), `negative/*.wav`
  the talking, `person.json` the name. Delete from the dashboard, or delete the folder.
- `voice/train/requests/` — a training request from the dashboard; `voice/train/status.json` — the
  trainer's progress; `voice/train/candidates/hey_newman-<date>.onnx` + `.metrics.json` — a finished
  new version waiting for *Use it*; `voice/train/last-run.log` — the last run's output.
- `voice/hey_newman.onnx` / `.json` (threshold) / `.metrics.json` — the version in use;
  `voice/hey_newman.previous.*` — the one before (for Undo).

**The trainer container** — `newman-wakeword-trainer`, CPU only, idle (and using no CPU) until asked.
It uses the training setup in `/mnt/cache/appdata/newman/training/` (openWakeWord, the
Python environment, the computer-voice clips and background audio — made once by
`scripts/pipeline.sh`, see below; that folder lives on the NAS and isn't part of this repository or of
Newman's backups: ~10 GB, all of it made again by the pipeline). Build and start it:

```bash
docker build -t newman-wakeword-trainer /mnt/cache/appdata/newman/training
docker run -d --name newman-wakeword-trainer --restart unless-stopped --network none \
  --shm-size 8g --cpus 8 \
  -v /mnt/cache/appdata/newman/training:/work \
  -v /mnt/cache/appdata/newman/voice:/voice \
  newman-wakeword-trainer
```

(or `docker compose --profile trainer up -d`, after the `docker build` above). `--shm-size 8g` matters (Docker's
64 MB default crashes training); `--cpus 8` leaves the NAS some room. It needs no network. A full run
with the default settings takes about **20–30 minutes** on the i5-10400 (measured: a 10 000-step run
with three voices took 8 minutes, most of it fixed checking work; the default 50 000 steps add ~7 more)
and uses up to ~8 GB of RAM while it trains; the dashboard shows the progress. If the dashboard says “The trainer isn't running”, start
the container; `docker logs newman-wakeword-trainer` and `voice/train/last-run.log` show what happened.

**Settings** are in `/mnt/cache/appdata/newman/training/scripts/hey_newman.yml`: `steps`
(50000), `layer_size` (32), `max_negative_weight` (1500 — higher = fewer false wake-ups but more
misses), `real_augmentation_rounds` (60 copies per recording), `real_holdout` (0.2) and
`max_false_wakes_per_hour` (1.0 — the threshold chosen is the lowest that stays under it).

**The computer-voice baseline** (once, already done on this NAS): openWakeWord's automatic pipeline
(synthetic “hey newman” clips from Piper's LibriTTS generator, look-alike phrases such as “hey norman”,
“hey google” and “new man” as negatives, room echo and AudioSet background noise, ~530 h of
openWakeWord's precomputed negative features) — `scripts/pipeline.sh` in a `python:3.10-slim`
container with `--shm-size 8g` (each stage leaves a `train/.done-*` marker so a re-run resumes; it
saves only the ONNX model, the tablets don't use tflite). `scripts/train_real.py` is one run with the
real recordings (what the trainer runs), `scripts/trainer.py` the container's loop.

## Apps

Native apps for iOS and Android, specified once in [`docs/apps.md`](docs/apps.md):

- **Newman** (phone, `ca.nimaste.newman`) — your phone layout's pages as tabs, every device and reading,
  Face ID / fingerprint before unlock-type commands, camera view, alerts, push notifications with
  action buttons and snapshots.
- **Newman Wall** (tablet, `ca.nimaste.newman.wall`) — the `/wall` screen in a kiosk shell: full
  screen, starts at boot, dims when idle, wakes on touch, client certificate built in. Runs without
  Google services, so Amazon Fire tablets work.

Both carry the client certificate (`.p12`) themselves and present it on every connection.

| | Build | Details |
|---|---|---|
| Android | GitHub Actions (`release.yml`): signed `Newman_<tag>.apk` and `NewmanWall_<tag>.apk` on each `v*` release; or on any Docker host (`android/README.md` has the command). Sideload the APKs | [`android/README.md`](android/README.md) — Fire tablet setup, device-owner kiosk, Firebase, release signing |
| iOS | GitHub Actions on macOS (`release.yml`): Ad Hoc `Newman_<tag>.ipa` and `NewmanWall_<tag>.ipa` on each `v*` release | [`ios/README.md`](ios/README.md) — Apple Developer setup, GitHub secrets, Guided Access |

### Connecting the apps

No app is tied to one house. The first time it opens, an app shows **Connect to your Newman** — before
anyone signs in — and the sign-in screen's **Server** button comes back to it:

- **On this network**: Newman announces itself over mDNS (`_newman._tcp`, on its HTTP port, named after
  the home), so phones and tablets at home list it; tap it.
- **Scan QR code**: Household → Accounts → an account's ⋯ → **Connect a device** (a wall tablet's account
  has the button right on it) shows a QR code of the connect link
  `newman://connect?v=1&id=…&name=…&local=…&outside=…` — the hub's id, name, home address and outside
  address, nothing secret. Opening that link on a phone works too.
- **Address**: typed in, then **Test connection** (`GET /api/hello`, which needs no sign-in). It says
  what's wrong in plain words: can't reach it, it needs a client certificate, it needs a Cloudflare
  Access token, its certificate isn't trusted, or it isn't a Newman hub.
- **Connection security** (same screen): the client certificate (`.p12` + password) and the Cloudflare
  Access service token (client id + secret).

The app keeps both addresses. It uses the home one while `/api/hello` there answers as the same hub (a
quick check on every network change and when the app comes back to the front), and moves to the outside
one only when home has failed for 2 minutes in a row or the phone's network changed — so a hub restart
never sends it outside — and only if the outside one answers as the same hub.
The wall tablet keeps the address it's on while it answers (its sign-in belongs to that address).
Apps that were set up before this keep their saved address and stay signed in.

The hub's **name** and **outside address** (e.g. `https://home.example.com/`) are set under
**Sign-in settings → This Newman**; they go into `/api/hello`, the mDNS announcement and the QR code.
Without an outside address set there, `PUBLIC_URL` is used.

### The phone's Home

One calm Home screen, worked out by the hub so both apps say the same thing: a line at the top that's
green when all is well ("Locked up · doors closed · no leaks"), amber with each device problem nobody muted,
red when something needs you now - the alarm going off, a leak, or a door, garage or lock left open for
15 minutes (Settings › Devices, or the device's own time), with its fix (*Close*, *Lock*) right there. Under it
a card for every category that has devices - Cameras (the newest alert, or one featured camera chosen in
Settings › Cameras), Climate, Lighting, Security, Garage, Appliances, a category you made… - plus Today and
People, and up to 4 favourite devices. A category that turns up later (new devices of a new kind) arrives
switched off and marked NEW. The admin sets the **household layout** (web › Dashboards › **Phone Home**:
cards, order, favourites) and, per person, whether they may change their own; *Edit Home* on the phone
reorders, hides and shows cards and picks favourites for that person only, and *Reset to household* undoes it.

**Categories.** Each device is in its kind's category unless an admin moves it (web › a device › Category:
any category, or *New category…* - "Pool"), and can be given another icon. A category made that way
gets its own Home card and goes away when nothing's left in it. Climate › Edit picks the rooms the Climate screen shows, for the
household; its comfort range and colours are in Settings › Climate.

**Groups and rooms.** Several lights or switches can be made into one group (Kitchen Under Cabinet): one row
everywhere, switched and dimmed together, on while any of them is, its brightness the average of those on;
its lights stay behind *Show the N lights*. A device is in one group at most. Groups and a device's **room**
(which otherwise comes from its connection) are set by admins - on the web, Devices › **Light groups** (name,
room, which lights) and the room by a device's name on its page; the Lights screen is grouped by room.

**Quiet hours** (web › Profile, each person's own): from-to and days in which that person's phones get
notifications without a sound - except what they allow through: the alarm, leaks and smoke; the doorbell;
people on cameras; automations marked urgent. Every notification carries its alert, so tapping one of any
age opens it in the app (camera ones play the clip) rather than an expired link.

### Settings

The web's **Settings** page (admins change, everyone sees): **Devices** - the low-battery level and how long something may be left open; **Lights** -
how long a full brighten/dim while held takes (3–6 s), a tap's step (5/10/20 %), how long a Pico button is
held before it ramps; **Climate** - the comfort range and colours; **Cameras** - Home's latest alert or one
featured camera, the default wait for camera automations, how many days alert videos are kept (1–7);
**History** - how many days Activity and automation runs are kept (1–90).

### Home-screen widgets and quick tiles

The phone apps put devices on the home screen. Any device a widget can show is offered — anything with
a one-tap command, a reading, or more than one state (not cameras) — and a widget shows each one's icon,
name and state (or its reading, exactly as reported). Tapping a device does what tapping its tile in the
app does (toggles a light, unlocks a locked lock, starts a docked vacuum); tapping a sensor opens it in
the app. Both phones offer three widgets: **Devices – tiles** and **Devices – rows** (you pick which
devices and how many - 1-2 on a small widget, up to 4 on a medium one, up to 8 on a large one - and the
layout grows to fill the widget), and **Home status** ("All secure" in green, or in red what isn't -
"Side Door unlocked" - with a button to lock or close it).
What a tap does comes from the hub (`GET /api/widgets/state`, worked out from the kind of device), so
every app agrees and a new kind of device needs nothing in the apps.

- **Android**: add **Devices – tiles** or **Devices – rows** (any size, resizable), pick its devices —
  search, by room or by type, in the order you want, up to 8; the widget shows as many as its size
  takes — and choose whether sensitive actions (unlock, open, disarm) ask first (off by default).
  Long-press the widget to change it. **Home status** needs no setup. Three **Quick Settings tiles** (Newman 1, 2, 3):
  add them to the shade, long-press one to pick its device; it shows on / off and runs the action (a
  sensitive one when the phone is locked asks you to unlock first). Widgets refresh on a tap, while the
  app is open (its live connection), and every 15 minutes.
- **iOS**: see [`ios/README.md`](ios/README.md).

When something a widget shows changes, the hub sends the phone a reload push — at most one every 30 s per
phone, and only for the devices that phone's widgets show: an APNs widget push to iPhones (needs the
APNs settings), an FCM message to Android phones when FCM is set up (it isn't required). Signing out
stops them. Details: [API.md → Phone widgets](API.md#phone-widgets-and-quick-tiles).

### Live statuses (Live Activities and ongoing notifications)

Any device can show a **live status** on phones while it matters — on an iPhone a Live Activity (Lock
Screen and Dynamic Island), on Android an ongoing, silent notification — with its state, how long it's
been that way, and buttons from the device itself (Close, Lock…; sensitive ones like Open or Unlock ask
for Face ID / the fingerprint or screen lock first). Tapping it opens the device.

- On a device's page (admins), **Live status**: turn on *Show a live status on phones*, tick the states
  that count (from the device's kind: open, unlocked, on, triggered…) and who sees it (everyone, or
  chosen people). It shows while the device is in one of those states and ends when it leaves them,
  staying two minutes with its final state ("Garage Door closed", no buttons), then going.
- In an automation's **Then**, **Show a live status on phones** of any devices, to chosen people — until
  they're no longer in a state, for a while, or until something else happens (see [Automations](#automations)).
- Each person sees at most 5: beyond that the newest 4 stay their own and the rest share one "N more".
- **iPhone**: needs the APNs settings (the hub starts, updates and ends them by push; it never pushes just
  for the clock). Live Activities are drawn by the iOS 26 widget extension, so an older iPhone shows none;
  *Settings → Newman → Live Activities* turns them off. **Android**: the *Live status* notification
  channel (silent); they follow along while the app runs, and with the app closed too when FCM is set up.
- Details: [API.md → Live statuses](API.md#live-statuses), [`ios/README.md`](ios/README.md#live-status-live-activities-phone),
  [`android/README.md`](android/README.md#live-statuses-phone).

## Deploying behind Caddy

Newman is meant to sit behind Caddy with client-certificate (mTLS) checks for family phones. Example
block (register `newman` in MTLSAuthManager first):

```caddy
home.{$MY_DOMAIN} {
  import mtls newman
  import secure_headers
  reverse_proxy http://10.1.1.2:8093 {
    header_up X-Client-Cert-Fingerprint {http.request.tls.client.fingerprint}
    header_up X-Client-Cert-Subject {http.request.tls.client.subject}
  }
}
```

Without a client certificate, the away policy decides (by default: password + 2FA code).

## Settings (environment variables)

Almost everything is configured in the dashboard — every connection (MQTT, Reolink and the rest) is
added there, under *Connections*. The few settings below are written out directly in
the `environment:` block of the `newman` service in [`docker-compose.yml`](docker-compose.yml) — edit
them there. Each has a comment; the ones you can leave alone are commented out with the value Newman uses
when it's unset. None of them is a secret: Newman keeps its own in the data folder, and passwords for
connections are entered in the dashboard.

| Variable | Default | |
|---|---|---|
| `TOKEN_SECRET` | the data folder's `.token_secret` | Signs notification links/tokens (snapshot links, buttons). Optional: unset, Newman uses `.token_secret` from the data folder, making it (random, `0600`, logged once) on first start; it's in backups, so a restore keeps every phone and tablet working. Set it (`openssl rand -hex 32`) only to pin your own — it then wins over the file and over restores |
| `PUBLIC_URL` | — | Address phones use to reach Newman (links in notifications) |
| `PUBLIC_TOKEN_URL` | `PUBLIC_URL` | Optional separate no-mTLS host for `/api/public/*` token links |
| `PORT` | `8093` | |
| `NEWMAN_MDNS` | `1` | `0` = don't announce the hub on the home network (`_newman._tcp`; the apps then connect by address or QR code) |
| `MDNS_INTERFACE` | — | Network interfaces to announce on and list as home addresses, e.g. `br0` (default: every one except Docker's and VPNs') |
| `DATA_DIR` | `/data` | Database, connection data, logs (set by the image; mount your folder there) |
| `APP_VERSION`, `WEB_DIR` | the release tag (`dev` for local builds), `/app/web` | Set by the image; nothing to change |
| `TZ` | — | Your time zone, e.g. `America/Toronto` (times and schedules use it) |
| `APNS_KEY_PATH` | — (compose: `/app/keys/apns-key.p8`) | iOS push: the APNs `.p8` key. Push is on when this, `APNS_KEY_ID` and `APNS_TEAM_ID` are all set |
| `APNS_KEY_ID`, `APNS_TEAM_ID` | — | The key's id and your Apple Developer team id |
| `APNS_BUNDLE_ID` | `ca.nimaste.newman` | The phone app's bundle id (push topics are made from it) |
| `FCM_SERVICE_ACCOUNT_PATH` | — (compose, commented out: `/app/keys/firebase-service-account.json`) | Android push: Firebase's service-account JSON. Set it only once the file is there — a missing file stops Newman from starting |
| `MAPKIT_KEY_ID` | — | Apple Maps (MapKit JS) key id; unset = OpenFreeMap maps (see People → The map) |
| `MAPKIT_TEAM_ID` | `APNS_TEAM_ID` | Apple Developer team of that key |
| `MAPKIT_KEY_PATH` | the APNs key file if `MAPKIT_KEY_ID` = `APNS_KEY_ID`, else `/app/keys/mapkit.p8` | MapKit key (`.p8`) |
| `VOICE_STT` | — | Speech-to-text for voice: a Wyoming service, e.g. `127.0.0.1:10300` (wyoming-whisper); unset = no voice |
| `VOICE_TTS` | — | Text-to-speech: e.g. `127.0.0.1:10200` (wyoming-piper); unset = replies shown, not spoken |
| `VOICE_TTS_KIND` | `wyoming` | `wyoming` = Piper at `VOICE_TTS`; `openai` = an OpenAI-compatible speech service at `VOICE_TTS_URL` (Kokoro), with Piper as the fallback |
| `VOICE_TTS_URL` | — | The HTTP speech service for `VOICE_TTS_KIND=openai`, e.g. `http://127.0.0.1:8880` (Kokoro-FastAPI) |
| `VOICE_NAME` | `en_US-lessac-medium` (`af_heart` with `openai`) | The hub's default reply voice (each tablet can pick another) |
| `VOICE_LANGUAGE`, `VOICE_WAKEWORD` | `en`, `hey_newman` | Language sent to Whisper; the default wake word (a model's name in `DATA_DIR/voice/`; each tablet can pick another) |
| `VOICE_STT_CONTAINER`, `VOICE_STT_RESTART` | `newman-whisper`, `1` | The Whisper container Newman restarts (Docker connection) when the hint changes; `0` = never (see Voice → The Whisper hint) |

## Data and backups

Everything Newman and its companion containers keep lives in one folder, `/mnt/cache/appdata/newman/`
on Unraid (`/data` in Newman's container; each companion mounts its own subfolder of it):

| Path | | In backups |
|---|---|---|
| `newman.db` | accounts, sessions, connections and their settings, automations, dashboards, phone layouts, activity | yes |
| `integrations/<id>/` | per-connection data: Roborock / Home Connect sessions, LG client id, caches (files are `0600`) | yes |
| `people/`, `wall-pictures/` | people's photos; the Christmas theme's own pictures | yes |
| `voice/` | wake-word models (the trained “Hey Newman” and the one before it, the stock ones), Teach recordings, training results, the Whisper hint (see [Voice](#teaching-and-training-hey-newman)). Also mounted by `newman-whisper` (`/hint`, read-only) and `newman-wakeword-trainer` (`/voice`) | yes, except a waiting training request |
| `matter/` | the `newman-matter` container's data: **Newman's Matter fabric** and every device paired to it (owned by uid 1000) | yes, except its lock and process-id files |
| `zigbee/` | the `newman-zigbee` container's data (Zigbee2MQTT): the network key and settings (`configuration.yaml`), the paired devices (`database.db`), the coordinator's own backup (`coordinator_backup.json`) | yes, except `log/` |
| `whisper/`, `piper/` | the speech containers' downloaded models and voices (`newman-whisper`, `newman-piper`) | no — downloaded again when missing |
| `training/` | the wake-word trainer's setup (openWakeWord, its Python environment, ~10 GB of generated clips and background audio; `newman-wakeword-trainer`'s `/work`) | no — made again by its `scripts/pipeline.sh` |
| `snapshots/`, `clips/` | notification snapshots; camera alert videos fetched from Frigate (kept 3 days) | no |
| `map-tiles/` | the hub's copies of map data (OpenFreeMap); safe to delete, fetched again when needed | no |
| `logs/newman.log` | the hub's log - the same lines as `docker logs newman`, but kept when the container is replaced on a deploy. Rotates at 5 MB, keeping 5 older files (`newman.log.1` … `.5`). Every notification / alert button press is logged on arrival with its outcome (accepted, or refused with 401 / 409 and why) | no |
| `backups/` | the nightly backups (below) | no |
| `.token_secret` | the secret that signs notification links (unless `TOKEN_SECRET` is set); made on first start, `0600` | yes |
| `.install_id` | generated on first start | yes |
| `.local_token` | generated on first start; only for the hub's own loopback requests, made again when missing | no |
| `keys/` | push keys, mounted read-only at `/app/keys`: `apns-key.p8` (the team's APNs key, Key ID `CGF89L7S4R`), `firebase-service-account.json` | no — keep the originals elsewhere too |

**Backups** (admins: the menu at the bottom of the sidebar → *Backups*): a nightly backup (03:30 by
default; the time, how many to keep — 7 — and on / off are set there) saved in `backups/` as a
`.newmanbackup` file; *Back up now*; downloading one (optionally locked with a passphrase of 8+
characters); and restoring from a downloaded file or a saved backup — the hub restarts into it, keeping
what was there before in `.pre-restore-<time>/`. A backup is meant to put you back exactly where you were
— every account, connection, automation and dashboard, every Matter and Zigbee device still paired, the
trained wake word and the recordings it was trained on — so it holds `newman.db` (copied safely while
running), `.install_id`, `.token_secret`, `integrations/`, `people/`, `wall-pictures/`, `voice/`,
`matter/` and `zigbee/` (see the table for the few files left out). What it leaves out comes back by
itself or isn't a setting: the speech models, the training setup, snapshots, clips, logs, map tiles,
earlier backups, and the push keys (files you keep). `matter/` and `zigbee/` are written by their
containers while Newman runs; Newman only reads them, and reads a folder again if it changed meanwhile,
so the copy is never half old, half new. Keep a copy off the server too: `backups/` lives in the data
folder.

**Restoring Matter and Zigbee:** the restore swaps `matter/` and `zigbee/` in as whole folders; the
running `newman-matter` and `newman-zigbee` containers keep writing to the old ones (set aside in
`.pre-restore-<time>/`) until they restart. So once Newman is back up after a restore, restart them —
`docker restart newman-matter newman-zigbee` — and they load the restored fabric and network. Restored
folders get the owner they had when backed up (`matter/` stays uid 1000). A backup made before these
folders were included leaves `voice/`, `matter/` and `zigbee/` as they are.

**Moving to this layout** (an install from before everything lived under one folder: the speech models
in `newman-voice/`, the Matter server's data in `newman-matter/`, the trainer's setup in
`newman-build-cache/voice/`): stop the companions, move each folder to its new place, then start them
with this `docker-compose.yml`:

```bash
cd /path/to/this/repository        # where docker-compose.yml is
docker stop newman-whisper newman-piper newman-matter newman-wakeword-trainer
A=/mnt/cache/appdata
mv $A/newman-voice/whisper        $A/newman/whisper
mv $A/newman-voice/piper          $A/newman/piper
mv $A/newman-matter               $A/newman/matter     # mv keeps its owner (uid 1000)
mv $A/newman-build-cache/voice    $A/newman/training
docker compose --profile voice --profile matter --profile trainer up -d
```

The moved folders must not exist yet at their new places (a container started with the new file before
the move creates them empty: remove those first). Nothing in Newman's own data moves.

Because the token secret is in the backup, restoring one — even onto a fresh install with an empty data
folder — keeps every phone and tablet working, links in notifications already sent included: the restore
writes it back as `.token_secret` (`0600`). If `TOKEN_SECRET` is set in `docker-compose.yml`, that one wins
and the backup's isn't written back; anyone pinning it that way keeps their own copy of it. A backup made
before the secret was included leaves the current one alone. Saved backups are `0600`, and since a
backup holds the connections' sessions, this secret, the Matter fabric's keys and the Zigbee network
key, lock downloads with a passphrase.

## Development

```bash
docker build -t newman:dev .
docker run --rm --network none -v "$PWD/server/tests:/app/tests" newman:dev \
  python -m unittest discover -s tests
```

The tests (673 of them, a little under 3 minutes) fake every device and service (MQTT, Home Connect, LG, Docker,
printers, Roborock…), so they need no network or credentials. API reference: [`API.md`](API.md). The web
page's own rules (when a page may reload itself for a new build) have plain-node tests: `cd web && npm test`.
App tests: [`android/README.md`](android/README.md#tests), [`ios/README.md`](ios/README.md#build-on-a-mac).

The web dashboard lives in `web/` (React + Vite); the image builds it in its first stage.

### Releases

Pushing a `v*` tag (e.g. `v1.2.0` — this project's releases are pushed with Hotwire) runs
`.github/workflows/release.yml` on GitHub:

- **build-and-push** — the hub's image to `ghcr.io/nimaste/newman` as `:1.2.0`, `:1.2` and `:latest`
  (`APP_VERSION` = the tag), and the GitHub Release with the tag's message as its notes.
- **android** — release-signed `Newman_<tag>.apk` and `NewmanWall_<tag>.apk` (version from the tag), attached
  to the Release.
- **ios** — Ad Hoc `Newman_<tag>.ipa` and `NewmanWall_<tag>.ipa`, attached to the Release.
- **sync-to-gcloud** — once all three are done, copies the Release's files to the NAS over WebDAV and emails
  the notes; skipped without its secrets.

It can also be run by hand (*Actions → Release → Run workflow*, with a tag). `ios-build.yml` builds both
iOS apps unsigned on every push that touches `ios/`.

Repository secrets (*Settings → Secrets and variables → Actions*; names only — never commit the values):

| Secret | For |
|---|---|
| `GHCR_PAT` | a token with `write:packages`, to push the image |
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD` | the shared release keystore (base64) and its password; key alias `newman` — see [`android/README.md`](android/README.md#release-signing) |
| `GOOGLE_SERVICES_JSON` | `android/phone/google-services.json`, base64 — phone push (FCM); without it the phone APK builds without push |
| `IOS_DISTRIBUTION_CERT_BASE64`, `IOS_CERT_PASSWORD`, `IOS_KEYCHAIN_PASSWORD`, `IOS_PROVISIONING_PROFILE_BASE64`, `IOS_PROVISIONING_PROFILE_NOTIFICATION_BASE64`, `IOS_PROVISIONING_PROFILE_LOCATION_BASE64`, `IOS_PROVISIONING_PROFILE_WIDGETS_BASE64`, `IOS_PROVISIONING_PROFILE_WALL_BASE64` | iOS signing — see [`ios/README.md`](ios/README.md#github-actions) |
| `GCLOUD_WEBDAV_USERNAME`, `GCLOUD_WEBDAV_PASSWORD`, `GCLOUD_BASE_URL`, `GCLOUD_SHARE_TOKEN`, `RELEASE_NOTIFY_EMAILS`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | optional: the copy to the NAS and the release email (set from Hotwire) |
