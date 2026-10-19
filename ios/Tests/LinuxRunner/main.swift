import Foundation

// A tiny test runner for the pure Shared/Core code, compiled with swiftc on Linux (no XCTest,
// no Xcode): decodes JSON captured read-only from a live hub (Tests/Fixtures) and checks the
// tile flow, controls, sensitive commands, live messages, sign-in replies and push actions.
//   Tests/run-linux-checks.sh  (Docker: swift:6.0)

var failures = 0
var passes = 0

func check(_ ok: Bool, _ what: String, file: String = #file, line: Int = #line) {
    if ok {
        passes += 1
    } else {
        failures += 1
        print("FAIL [\(line)] \(what)")
    }
}

func eq<T: Equatable>(_ a: T, _ b: T, _ what: String, line: Int = #line) {
    check(a == b, "\(what): got \(a), expected \(b)", line: line)
}

let fixtures: URL = {
    let arg = CommandLine.arguments.dropFirst().first ?? "Tests/Fixtures"
    return URL(fileURLWithPath: arg, isDirectory: true)
}()

func load<T: Decodable>(_ name: String, as type: T.Type) -> T {
    let url = fixtures.appendingPathComponent(name + ".json")
    do {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(T.self, from: data)
    } catch {
        print("FATAL: couldn't decode \(name).json: \(error)")
        exit(2)
    }
}

// MARK: decoding real hub replies

let meta = load("meta", as: Meta.self)
let kinds = meta.kindsById
check((meta.kinds ?? []).count >= 20, "meta has kinds (\((meta.kinds ?? []).count))")
let entities = load("entities", as: [Entity].self)
check(entities.count > 10, "entities decoded (\(entities.count))")
let byId = Dictionary(uniqueKeysWithValues: entities.map { ($0.id, $0) })
check(entities.allSatisfy { kinds[$0.kind] != nil }, "every entity's kind is in meta")
let dashboard = load("dashboard_current", as: Dashboard.self)
check((dashboard.pages ?? []).count >= 3, "dashboard pages")
let tiles = load("dashboard_tiles", as: JSONValue.self)
check((tiles["types"]?.arrayValue ?? []).count >= 9, "tile types")
let categories = load("push_categories", as: [PushCategory].self)
check(!categories.isEmpty && categories.allSatisfy { $0.id.hasPrefix("c_") && !$0.buttons.isEmpty }, "push categories")
let alerts = load("alerts", as: [CameraAlert].self)
check(!alerts.isEmpty && alerts.allSatisfy { $0.start != nil }, "camera alerts")
let events = load("events", as: [ActivityEvent].self)
check(!events.isEmpty && events.allSatisfy { !$0.id.isEmpty && $0.ts > 0 }, "activity events")
let auth = load("auth_state", as: AuthState.self)
check(auth.where != nil && auth.signedIn == false, "auth state")
eq(auth.isHome ? "at home" : auth.connectionText, auth.connectionText, "connection text")

// MARK: sensitive commands come from meta, not a list in the app

let sensitive = Set((meta.kinds ?? []).flatMap { k in k.commandList.filter { $0.isSensitive }.map { "\(k.id).\($0.id)" } })
check(sensitive.contains("lock.unlock") && sensitive.contains("alarm.disarm") && sensitive.contains("garage.open"),
      "sensitive: \(sensitive.sorted())")
check(!sensitive.contains("lock.lock"), "locking is not sensitive")
if let unlock = kinds["lock"]?.command("unlock"), let lock = kinds["lock"]?.command("lock") {
    check(DeviceLogic.needsAuthentication(unlock, biometricsEnabled: true), "unlock asks for Face ID")
    check(!DeviceLogic.needsAuthentication(unlock, biometricsEnabled: false), "switch off = no Face ID")
    check(!DeviceLogic.needsAuthentication(lock, biometricsEnabled: true), "lock needs no Face ID")
} else {
    check(false, "lock kind has lock/unlock")
}

// MARK: valueType -> control

var seenControls: [String: Int] = [:]
for k in meta.kinds ?? [] {
    for c in k.commandList {
        let ctl = CommandControl.forCommand(c)
        let key: String
        switch ctl {
        case .button: key = "button"
        case .slider: key = "slider"
        case .stepper: key = "stepper"
        case .statePicker: key = "state"
        case .choices: key = "choices"
        case .text: key = "text"
        }
        seenControls[key, default: 0] += 1
        if !(c.takesValue ?? false) { eq(key, "button", "\(k.id).\(c.id) without a value is a button") }
    }
}
for key in ["button", "slider", "stepper", "state", "choices", "text"] {
    check((seenControls[key] ?? 0) > 0, "meta has a command needing a \(key)")
}
if let t = kinds["thermostat"]?.command("set_temperature") {
    eq(CommandControl.forCommand(t), .stepper(min: 5, max: 35, step: 0.5, unit: "°C"), "set_temperature stepper")
    eq(CommandControl.snapped(21.26, t), 21.5, "snap to 0.5")
    eq(CommandControl.snapped(99, t), 35, "clamp to max")
}
if let b = kinds["light"]?.command("set_brightness") {
    eq(CommandControl.forCommand(b), .slider(min: 1, max: 100, step: 1, unit: "%"), "set_brightness slider")
}

// MARK: device wording (web/src/model.js)

do {
    // The hub's own wording (kind notes: a wall tablet with its screen off) wins over the state's label.
    var tablet = Entity(id: "wall.tablet", name: "Wall", kind: "tablet", state: "charging")
    eq(DeviceLogic.stateLabel(tablet, nil), "charging", "no hub label: the state")
    tablet.stateLabel = "Screen off"
    eq(DeviceLogic.stateLabel(tablet, nil), "Screen off", "hub stateLabel wins")
    eq(DeviceLogic.stateText(tablet, nil), "Screen off", "device text uses the hub's label")
    tablet.stateLabel = ""
    eq(DeviceLogic.stateLabel(tablet, nil), "charging", "empty hub label ignored")
}

if let th = byId["homekit.thermostat_1"] {
    let txt = DeviceLogic.stateText(th, kinds[th.kind])
    check(txt.contains("°C →") || txt.hasPrefix("Off"), "thermostat text: \(txt)")
}
for e in entities {
    let k = kinds[e.kind]
    let txt = DeviceLogic.stateText(e, k)
    check(!txt.isEmpty || (k?.stateList.isEmpty ?? true), "\(e.id) has state text")
    if let a = DeviceLogic.tapAction(e, k) { check(!(a.takesValue ?? false), "tap action is plain: \(e.id)") }
    _ = DeviceLogic.extraActions(e, k, main: DeviceLogic.tapAction(e, k))
    for p in e.properties ?? [] { _ = DeviceLogic.formatProp(p, p.value) }
}
if let lock = entities.first(where: { $0.kind == "lock" }), let k = kinds["lock"] {
    let next = DeviceLogic.nextAction(lock, k)
    check(next != nil && next?.confirmsTo != lock.state, "lock's next action flips it (\(next?.id ?? "none"))")
    let after = DeviceLogic.optimistic(lock, k, next!, value: nil)
    eq(after.state, next!.confirmsTo, "optimistic state")
}
if let light = entities.first(where: { $0.kind == "light" }), let k = kinds["light"], let dim = k.command("set_brightness") {
    let after = DeviceLogic.optimistic(light, k, dim, value: .number(40))
    eq(after.attr("brightness"), .number(40), "optimistic brightness")
    eq(after.state, "on", "dimming turns it on")
    let tap = DeviceLogic.tapAction(light, k)
    eq(tap?.id, light.state == "on" ? "turn_off" : "turn_on", "light tap toggles")
}
if let vac = entities.first(where: { $0.kind == "vacuum" }), let k = kinds["vacuum"] {
    let extras = DeviceLogic.extraActions(vac, k, main: DeviceLogic.tapAction(vac, k))
    check(extras.contains { $0.resolvedValueType == "choices" }, "vacuum offers its rooms")
}
eq(DeviceLogic.humanize("linkQuality"), "Link quality", "humanize")
eq(DeviceLogic.shortEventLabel("“Favorite” is pressed"), "Favorite", "short event label")
eq(DeviceLogic.shortEventLabel("“Button 1” is long-pressed"), "Button 1", "a long press names its button")
eq(DeviceLogic.shortEventLabel("“Button 1” is let go after being long-pressed"), "Button 1", "so does letting go")
eq(DeviceLogic.shortEventLabel("Person detected"), "Person", "other events keep their wording")
eq(DeviceLogic.countList(["person": .number(2), "car": .number(1), "dog": .number(0)]), ["1 car", "2 people"], "counts")

// MARK: dashboard -> phone grid

for page in dashboard.pages ?? [] {
    let cells = PhoneLayout.cells(for: page, areaOf: { byId[$0]?.area }, exists: { byId[$0] != nil })
    let rows = PhoneLayout.rows(cells)
    check(rows.allSatisfy { $0.used <= 4 }, "\(page.name): no row wider than 4")
    check(rows.allSatisfy { $0.used == 4 || $0.cells.count == 1 || $0 == rows.last }, "\(page.name): rows are full")
    eq(Set(rows.flatMap { $0.cells.map { $0.id } }).count, cells.count, "\(page.name): every cell placed once")
    print("  \(page.name): " + rows.map { r in r.cells.map { c -> String in
        switch c.content {
        case .tile(let t): return "\(t.type)(\(c.span))"
        case .device(_, let id): return "\(byId[id]?.name ?? id)(\(c.span))"
        case .heading(let s, _): return "[\(s)]"
        }
    }.joined(separator: " + ") }.joined(separator: " | "))
}
let mixed = DashboardPage(id: "p", name: "Test", icon: nil, tiles: [
    Tile(id: "a", type: "weather", x: 0, y: 0, w: 3, h: 2),
    Tile(id: "b", type: "camera", x: 3, y: 0, w: 6, h: 4),
    Tile(id: "c", type: "dial", x: 9, y: 0, w: 3, h: 3),
])
let mixedRows = PhoneLayout.rows(PhoneLayout.cells(for: mixed))
eq(mixedRows.map { $0.cells.map { $0.id } }, [["a", "c"], ["b"]], "a half tile fills the gap beside another")

// MARK: dial, weather, calendar

if let th = byId["homekit.thermostat_1"], let tile = (dashboard.pages ?? []).flatMap({ $0.tiles ?? [] }).first(where: { $0.type == "dial" }),
   let dial = DialModel(entity: th, kind: kinds[th.kind], tile: tile) {
    eq(dial.decimals, 1, "dial decimals from step 0.5")
    eq(dial.angle(for: dial.min), 135, "dial start angle")
    eq(dial.angle(for: dial.max), 405, "dial end angle")
    eq(dial.value(atDegrees: 270), 20, "dial top = middle")
    check(!dial.modes.isEmpty, "dial has mode chips")
    check(dial.nowLine(compact: true).hasPrefix("Now "), "dial now line: \(dial.nowLine(compact: true))")
} else {
    check(false, "dial tile decodes")
}
if let w = WeatherModel.pick(tile: nil, entities: entities) {
    let wm = WeatherModel(entity: w, kind: kinds[w.kind])
    check(wm.temperature != nil && !wm.forecast.isEmpty, "weather now + forecast")
    check(wm.highLowLine().hasPrefix("H "), "weather H/L: \(wm.highLowLine())")
}
let fixedNow = HubDates.parse("2026-10-01T12:00:00")!
let agenda = AgendaModel.items(tile: nil, entities: entities, now: fixedNow)
check(agenda.allSatisfy { $0.end > fixedNow }, "agenda only upcoming (\(agenda.count))")
eq(HubDates.dayKey(HubDates.parse("2026-10-03T07:18")!), "2026-10-03", "local date parse")
check(HubDates.parse("2026-10-01T19:59:29+00:00") != nil, "ISO date parse")
let problems = HouseStatus.attention(entities, kinds: kinds)
print("  attention: \(problems.map { "\($0.name): \($0.issues.joined(separator: ", "))" })")
print("  security: \(HouseStatus.securitySummary(entities, kinds: kinds) ?? "-")")

// MARK: live messages

let entityJSON = String(data: try! JSONEncoder().encode(["x": 1]), encoding: .utf8)!
check(entityJSON.contains("x"), "encoder works")
if case .hello(let t)? = LiveMessage.parse(#"{"type": "hello", "time": 1790000000.5, "version": "dev"}"#) {
    eq(t, 1790000000.5, "hello time")
} else { check(false, "hello parses") }
let entMsg = #"{"type": "entity", "entity": {"id": "x.1", "name": "Side", "kind": "lock", "state": "LOCKED", "attrs": {"batteryLevel": 90}}}"#
if case .entity(let e)? = LiveMessage.parse(entMsg) { eq(e.state, "LOCKED", "entity message") } else { check(false, "entity parses") }
let numState = #"{"type": "entity", "entity": {"id": "s.1", "name": "Temp", "kind": "temperature", "state": 21.5}}"#
if case .entity(let e)? = LiveMessage.parse(numState) { eq(e.state, "21.5", "numeric state kept as text") } else { check(false, "numeric state parses") }
let actMsg = #"{"type": "activity", "entry": {"id": 254, "ts": 1790899169.2, "kind": "alert", "title": "Person at Garage", "body": "Driveway", "entityId": "frigate.camera_garage", "data": {"thumbUrl": "api/x.webp"}}}"#
if case .activity(let a)? = LiveMessage.parse(actMsg) { eq(a.thumbPath, "api/x.webp", "activity thumb"); eq(a.id, "254", "activity id") } else { check(false, "activity parses") }
if case .dashboard? = LiveMessage.parse(#"{"type": "dashboard", "userId": "u1"}"#) { passes += 1 } else { check(false, "dashboard message") }
if case .settings? = LiveMessage.parse(#"{"type": "settings"}"#) { passes += 1 } else { check(false, "settings message") }
if case .event(let id, let ev)? = LiveMessage.parse(#"{"type": "event", "entityId": "d.1", "event": "pressed"}"#) {
    eq(id, "d.1", "event entity"); eq(ev, "pressed", "event name")
} else { check(false, "event parses") }
check(LiveMessage.parse("not json") == nil, "garbage ignored")
if case .entityRemoved(let id)? = LiveMessage.parse(#"{"type":"entity-removed","id":"zwave.12"}"#) { eq(id, "zwave.12", "entity-removed id") } else { check(false, "entity-removed parses") }
check(LiveMessage.parse(#"{"type":"entity-removed"}"#) == nil, "entity-removed without an id is ignored")
if case .unknown(let u)? = LiveMessage.parse(#"{"type":"entity-renamed","id":"x"}"#) { eq(u, "entity-renamed", "unknown types still ignored") } else { check(false, "unknown type") }
let devs = Dictionary(uniqueKeysWithValues: entities.prefix(3).map { ($0.id, $0) })
let goneId = entities[1].id
let afterRemoval = EntityUpdate.apply(devs, .entityRemoved(goneId))
check(afterRemoval[goneId] == nil && afterRemoval.count == 2, "removed device dropped")
eq(EntityUpdate.apply(devs, .entityRemoved("not.here")).count, 3, "removing an unknown device changes nothing")
eq(EntityUpdate.apply(afterRemoval, .entity(entities[1]))[goneId]?.id, goneId, "a new device arrives as an entity message")
eq(EntityUpdate.apply(devs, .pong).count, 3, "other messages leave devices alone")
var backoff = Backoff()
eq([backoff.take(), backoff.take(), backoff.take(), backoff.take(), backoff.take(), backoff.take(), backoff.take()],
   [1, 2, 4, 8, 16, 30, 30], "backoff")

// MARK: sign-in replies (server/newman/api.py auth_login)

let ok = LoginOutcome.classify(status: 200, body: Data(#"{"ok": true, "user": {"id": "u1", "username": "chris", "name": "Chris", "role": "admin"}, "token": "abc"}"#.utf8))
if case .signedIn(let tok, let user) = ok { eq(tok, "abc", "token"); eq(user?.name, "Chris", "user") } else { check(false, "200 signs in") }
eq(LoginOutcome.classify(status: 401, body: Data(#"{"needs": "totp"}"#.utf8)), .needsCode(message: nil), "needs totp")
eq(LoginOutcome.classify(status: 401, body: Data(#"{"needs": "totp", "error": "That code didn't work"}"#.utf8)),
   .needsCode(message: "That code didn't work"), "bad code")
eq(LoginOutcome.classify(status: 401, body: Data(#"{"ok": false, "error": "Wrong username or password"}"#.utf8)),
   .failed("Wrong username or password"), "wrong password")
eq(LoginOutcome.classify(status: 403, body: Data(#"{"ok": false, "error": "Sign in with your certificate from outside"}"#.utf8)),
   .failed("Sign in with your certificate from outside"), "403 shown as-is")
eq(LoginOutcome.classify(status: 502, body: Data("<html>".utf8)), .failed("The Newman hub isn't answering right now."), "502")
let lb = LoginOutcome.body(username: " chris ", password: "pw", code: "123456", kind: "app", deviceName: "Chris's iPhone")
eq(lb["kind"], .string("app"), "login kind"); eq(lb["code"], .string("123456"), "login code"); eq(lb["username"], .string("chris"), "trim")

// MARK: push

let userInfo: [AnyHashable: Any] = ["aps": ["category": categories[0].id], "action_url": "https://home.example/api/public/actions",
                                    "action_subject": "run_1", "action_exp": "1790999999", "action_token": "tok",
                                    "snapshot_url": "https://home.example/api/public/snapshots/s1?device=d&exp=1&token=t"]
if let req = PushPayload.actionRequest(userInfo: userInfo, button: categories[0].buttons[0].id, deviceId: "dev-1") {
    eq(req.url.absoluteString, "https://home.example/api/public/actions", "action url")
    eq(req.body, ["button": categories[0].buttons[0].id, "subject": "run_1", "deviceId": "dev-1", "exp": "1790999999", "token": "tok"], "action body")
} else { check(false, "action request") }
check(PushPayload.actionRequest(userInfo: ["kind": "test"], button: "dismiss", deviceId: "d") == nil, "no token, no request")
// A press goes to the address in use first (here home), then the other, the push's action_url last.
let publicAction = URL(string: "https://home.garito.ca/api/public/actions")!
eq(PushPayload.actionURLs(actionURL: publicAction, addresses: [URL(string: "http://10.1.1.2:8093/")!, URL(string: "https://home.garito.ca/")!])
    .map(\.absoluteString), ["http://10.1.1.2:8093/api/public/actions", "https://home.garito.ca/api/public/actions"],
   "action urls: home, then outside (= action_url, not twice)")
eq(PushPayload.actionURLs(actionURL: publicAction, addresses: [URL(string: "https://away.example/newman/")!, URL(string: "http://10.1.1.2:8093/")!])
    .map(\.absoluteString), ["https://away.example/newman/api/public/actions", "http://10.1.1.2:8093/api/public/actions",
                             "https://home.garito.ca/api/public/actions"], "action urls: outside in use, path prefix kept, action_url last")
eq(PushPayload.actionURLs(actionURL: publicAction, addresses: []).map(\.absoluteString), [publicAction.absoluteString],
   "no saved hub: only action_url")
check(PushPayload.tryNextAddress(status: nil), "no answer: try the next address")
check(PushPayload.tryNextAddress(status: 502) && PushPayload.tryNextAddress(status: 530), "proxy can't reach the hub: next")
check(!PushPayload.tryNextAddress(status: 409) && !PushPayload.tryNextAddress(status: 401) && !PushPayload.tryNextAddress(status: 200),
      "the hub's own answer is final")
check(PushPayload.actionAttemptSeconds * 3 < 30, "three tries fit in iOS's background time for a button")
// Try times: the first longer, all within the budget and iOS's background time (minus the reserve).
eq(PushPayload.attemptSeconds(index: 0, elapsed: 0, backgroundLeft: nil), 20, "first try: 20 s")
eq(PushPayload.attemptSeconds(index: 1, elapsed: 20, backgroundLeft: nil), 7, "second try: what's left of 27 s")
eq(PushPayload.attemptSeconds(index: 1, elapsed: 2, backgroundLeft: nil), 8, "a fallback try: 8 s")
eq(PushPayload.attemptSeconds(index: 2, elapsed: 26, backgroundLeft: nil), nil, "under 2 s left: no more tries")
eq(PushPayload.attemptSeconds(index: 0, elapsed: 0, backgroundLeft: 12), 9, "background time left caps a try (12 - 3)")
eq(PushPayload.attemptSeconds(index: 0, elapsed: 0, backgroundLeft: 4), nil, "almost no background time: no try")

// MARK: press reports

eq(PressReport.redact(URL(string: "https://user:pw@home.garito.ca/api/public/actions?token=secret#x")!),
   "https://home.garito.ca/api/public/actions", "redact: no query, credentials or fragment")
eq(PressReport.redact(URL(string: "http://10.1.1.2:8093/api/public/actions")!), "http://10.1.1.2:8093/api/public/actions", "redact keeps the port")
eq(PressReport.urls(addresses: [URL(string: "http://10.1.1.2:8093/")!, URL(string: "https://home.garito.ca/")!],
                    actionURL: URL(string: "https://home.garito.ca/api/public/actions")).map(\.absoluteString),
   ["http://10.1.1.2:8093/api/public/press-report", "https://home.garito.ca/api/public/press-report"], "report urls (action_url's hub not twice)")
eq(PressReport.urls(addresses: [], actionURL: URL(string: "https://x.example/newman/api/public/actions")).map(\.absoluteString),
   ["https://x.example/newman/api/public/press-report"], "report url from action_url alone")
let pressTimeout = NSError(domain: "NSURLErrorDomain", code: -1001, userInfo: [
    "NSLocalizedDescription": "The request timed out.",
    "NSUnderlyingError": NSError(domain: "kCFErrorDomainCFNetwork", code: -1001, userInfo: [NSLocalizedDescriptionKey: "timed out"]),
    "_kCFStreamErrorCodeKey": -2102, "_kCFStreamErrorDomainKey": 4,
    "NSErrorFailingURLKey": URL(string: "http://10.1.1.2:8093/api/public/actions")!,
    "_NSURLErrorNWPathKey": "satisfied (Path is satisfied), interface: en0[802.11]"])
let pe = PressError.of(pressTimeout)
eq(pe.code, -1001, "error code"); eq(pe.domain, "NSURLErrorDomain", "error domain")
eq(pe.underlying, "kCFErrorDomainCFNetwork -1001 timed out", "underlying error")
eq(pe.streamCode, -2102, "stream error code"); eq(pe.streamDomain, 4, "stream error domain")
eq(pe.failingHost, "10.1.1.2", "failing host")
check(pe.line.contains("path satisfied (Path is satisfied)") && pe.line.hasPrefix("NSURLErrorDomain -1001 The request timed out. ["), pe.line)
let start = Date(timeIntervalSince1970: 1_000)
eq(PressMetrics.offset(Date(timeIntervalSince1970: 1_000.0125), from: start), 13, "metrics offset in ms")
eq(PressMetrics.offset(nil, from: start), nil, "a step that never happened")
var pm = PressMetrics(transactions: 1)
pm.dnsStart = 0; pm.dnsEnd = 0; pm.connectStart = 1; pm.remoteAddress = "10.1.1.2"; pm.remotePort = 8093
pm.networkProtocol = "http/1.1"; pm.reusedConnection = false
eq(pm.line, "dns 0-0 connect 1-? tls - request - response - | 10.1.1.2:8093 http/1.1 new", "metrics line: never connected")
let attempt = PressAttempt(at: 1_790_000_000, url: "http://10.1.1.2:8093/api/public/actions", appState: "background",
                           backgroundLeft: 29.5, timeoutSeconds: 20, ms: 20004, status: nil, error: pe, certChallenges: 0,
                           certAvailable: nil, otherChallenges: nil, metrics: pm)
let al = PressReport.line(attempt, index: 0, of: 3)
check(al.hasPrefix("try 1/3 http://10.1.1.2:8093/api/public/actions (background, 29.5 s background left, 20 s allowed): 20004 ms, NSURLErrorDomain -1001"), al)
check(al.contains("cert challenges 0;") && al.hasSuffix("http/1.1 new"), al)
var okAttempt = attempt; okAttempt.status = 200; okAttempt.error = nil; okAttempt.ms = 85
eq(PressReport.summary(ok: true, outcome: "pressed", attempts: [attempt, okAttempt], tried: 3),
   "pressed at http://10.1.1.2:8093/api/public/actions on try 2 of 3 in 85 ms (background)", "success summary")
eq(PressReport.summary(ok: false, outcome: "failed: slow", attempts: [attempt, attempt], tried: 3),
   "failed: slow after 2 of 3 tries, 40008 ms in all (background)", "failure summary")
let report = PressReport(id: "r1", button: "lock:lock.laundry", subject: "run_1", deviceId: "ios-1", exp: "1", token: "t", ok: false,
                         outcome: "failed", summary: "s", appVersion: "1.0 (1)", launchState: "background", sinceLaunchSeconds: 0.4,
                         backgroundExpired: false, actionURL: "https://home.garito.ca/api/public/actions", at: 1, attempts: [attempt])
if let enc = try? JSONEncoder().encode(report), let back = try? JSONDecoder().decode(PressReport.self, from: enc) {
    eq(back, report, "press report round trip")
    let json = String(decoding: enc, as: UTF8.self)
    check(json.contains("\"certChallenges\":0") && json.contains("\"connectStart\":1") && json.contains("\"launchState\":\"background\""), json)
} else { check(false, "press report encodes") }
let d1 = Data("1".utf8), d2 = Data("2".utf8)
eq(PressReport.queued([d1, d2], adding: d1), [d2, d1], "queue: a report once, newest last")
eq(PressReport.queued((0..<12).map { Data("\($0)".utf8) }, adding: d2, limit: 10).count, 10, "queue keeps the last 10")
eq(PushPayload.snapshotURL(userInfo: userInfo)?.host, "home.example", "snapshot url")
eq(PushPayload.hexToken(Data([0x0a, 0xff, 0x01])), "0aff01", "hex token")
// A push's own button set, registered by the Notification Service Extension.
let pushedButtons = #"[{"id":"lock:lock.side","label":"Lock Side Door","icon":"lock","sensitive":false},{"id":"unlock:lock.side","label":"Unlock","icon":"unlock","sensitive":true},{"id":"open","label":"Open","icon":"home"},{"id":"dismiss","label":"Dismiss","icon":"x","sensitive":false}]"#
let pushedCat = PushPayload.category(userInfo: ["aps": ["category": "c_abc123"], "buttons": pushedButtons])
eq(pushedCat?.id, "c_abc123", "push category id")
eq(pushedCat?.buttons.map(\.id), ["lock:lock.side", "unlock:lock.side", "open", "dismiss"], "push category buttons")
eq(PushPayload.category(userInfo: ["category": "c_x", "buttons": pushedButtons])?.id, "c_x", "category outside aps")
check(PushPayload.category(userInfo: ["aps": ["category": "c_abc123"]]) == nil, "no buttons, no category")
check(PushPayload.category(userInfo: ["buttons": pushedButtons]) == nil, "no category id, nothing to register")
check(PushPayload.category(userInfo: ["aps": ["category": "c_x"], "buttons": "[]"]) == nil, "empty buttons")
check(PushPayload.category(userInfo: ["aps": ["category": "c_x"], "buttons": "not json"]) == nil, "bad buttons json")
if let b = pushedCat?.buttons, b.count == 4 {
    eq(PushPayload.actionStyle(b[0]), .init(authenticationRequired: false, foreground: false, destructive: false), "lock style")
    eq(PushPayload.actionStyle(b[1]), .init(authenticationRequired: true, foreground: false, destructive: false), "unlock needs Face ID")
    eq(PushPayload.actionStyle(b[2]), .init(authenticationRequired: false, foreground: true, destructive: false), "open is local")
    eq(PushPayload.actionStyle(b[3]), .init(authenticationRequired: false, foreground: false, destructive: true), "dismiss is red")
} else { check(false, "push category buttons parsed") }
// The extension always hands the notification back in time (iOS allows about 30 s).
check(PushPayload.extensionDeadlineSeconds + 3 <= 30, "deadline before iOS's own")
eq(PushPayload.snapshotTimeout(elapsed: 0), 20, "snapshot timeout")
eq(PushPayload.snapshotTimeout(elapsed: 10), 14, "snapshot timeout near the deadline")
eq(PushPayload.snapshotTimeout(elapsed: 40), 1, "snapshot timeout past the deadline")
check(PushPayload.shouldRefreshCategories(lastAt: nil, now: 1000), "first background refresh")
check(!PushPayload.shouldRefreshCategories(lastAt: 1000, now: 1000 + 60), "refreshed a minute ago")
check(PushPayload.shouldRefreshCategories(lastAt: 1000, now: 1000 + 15 * 60), "refresh after 15 min")
check(PushPayload.shouldRefreshCategories(lastAt: 5000, now: 1000), "clock moved back")
let reg = PushPayload.registerBody(token: "t", deviceId: "d", friendlyName: "Chris's iPhone", deviceModel: "iPhone16,1", sandbox: true)
eq(reg["platform"], .string("ios"), "register platform"); eq(reg["apnsEnvironment"], .string("sandbox"), "sandbox")

// Camera notifications open the alert's video.
let pushedAlert = PushPayload.alertClip(userInfo: ["clip": "frigate:r:1790899161.972472-1y29hf",
                                                   "frigate_event_id": "1790899161.972472-1y29hf", "camera": "garage"])
eq(pushedAlert?.clip, "frigate:r:1790899161.972472-1y29hf", "push clip")
eq(pushedAlert?.reviewId, "1790899161.972472-1y29hf", "push review id")
check(PushPayload.alertClip(userInfo: userInfo) == nil, "no clip, no alert")
eq(PushPayload.alertClip(userInfo: ["clip": "frigate:r:x", "frigate_event_id": ""])?.reviewId ?? "none", "none", "empty review id")
eq(AlertClip.path("frigate:r:1791546301.32669-9penfv"), "api/clips/frigate/r%3A1791546301.32669-9penfv", "clip path")
eq(AlertClip.path("my hub:a/b"), "api/clips/my%20hub/a%2Fb", "clip path encoded")
check(AlertClip.path(nil) == nil && AlertClip.path("frigate") == nil && AlertClip.path(":x") == nil && AlertClip.path("x:") == nil, "no clip path")
eq(AlertClip.match(alerts, clip: "frigate:r:1790899161.972472-1y29hf", reviewId: nil)?.id, "1790899161.972472-1y29hf", "match by clip")
eq(AlertClip.match(alerts, clip: "frigate:other", reviewId: "1790899110.48895-7ask38")?.id, "1790899110.48895-7ask38", "match by review id")
check(AlertClip.match(alerts, clip: "frigate:other", reviewId: nil) == nil, "no match")
if let base = URL(string: "https://home.example/"), let p = AlertClip.path("frigate:r:1.5-ab") {
    eq(ServerAddress.url(base, p)?.absoluteString, "https://home.example/api/clips/frigate/r%3A1.5-ab", "clip url")
} else { check(false, "clip url") }
// Streaming ranges (headers as the hub sends them for a finished clip).
eq(ClipRange(contentRange: "bytes 0-1/15477162"), ClipRange(contentRange: " BYTES 0-1/15477162 "), "content range case/space")
eq(ClipRange(contentRange: "bytes 0-1/15477162")?.total, 15477162, "content range total")
eq(ClipRange(contentRange: "bytes 1000-15477161/15477162")?.start, 1000, "content range start")
eq(ClipRange(contentRange: "bytes 1000-15477161/15477162")?.end, 15477161, "content range end")
check(ClipRange(contentRange: "bytes 0-1/*") != nil && ClipRange(contentRange: "bytes 0-1/*")?.total == nil, "content range unknown total")
check([nil, "", "bytes */100", "bytes 5-1/100", "bytes 0-100/100", "items 0-1/2", "bytes 0-1", "bytes a-b/3", "bytes -1-2/3"]
    .allSatisfy { ClipRange(contentRange: $0) == nil }, "bad content ranges")
eq(ClipRange.probe, "bytes=0-1", "probe range")
eq(ClipRange.header(offset: 0, length: 2, toEnd: false, total: 100), "bytes=0-1", "range first bytes")
eq(ClipRange.header(offset: 50, length: 10, toEnd: true, total: 100), "bytes=50-", "range to end")
eq(ClipRange.header(offset: 90, length: 50, toEnd: false, total: 100), "bytes=90-99", "range clamped")
eq(ClipRange.header(offset: 90, length: 50, toEnd: false, total: nil), "bytes=90-139", "range without total")
check(ClipRange.header(offset: 100, length: 1, toEnd: true, total: 100) == nil, "range past end")
check(ClipRange.header(offset: 10, length: 0, toEnd: false, total: 100) == nil, "empty range")
check(ClipRange.header(offset: -1, length: 5, toEnd: false, total: 100) == nil, "negative range")

// MARK: addresses

eq(ServerAddress.normalize("home.garito.ca")?.absoluteString, "https://home.garito.ca/", "https default")
eq(ServerAddress.normalize("10.1.1.2:8093")?.absoluteString, "http://10.1.1.2:8093/", "LAN http")
eq(ServerAddress.normalize("http://10.1.1.2:8093/#/wall")?.absoluteString, "http://10.1.1.2:8093/", "pasted wall link")
eq(ServerAddress.normalize("https://home.garito.ca/wall/")?.absoluteString, "https://home.garito.ca/", "pasted /wall")
check(ServerAddress.normalize("") == nil, "empty address")
let base = ServerAddress.normalize("https://home.garito.ca")!
eq(ServerAddress.webSocketURL(base)?.absoluteString, "wss://home.garito.ca/api/ws", "wss")
eq(ServerAddress.webSocketURL(ServerAddress.normalize("http://10.1.1.2:8093")!)?.absoluteString, "ws://10.1.1.2:8093/api/ws", "ws")
eq(ServerAddress.url(base, "api/entities/frigate.camera_garage/still.jpg")?.absoluteString,
   "https://home.garito.ca/api/entities/frigate.camera_garage/still.jpg", "relative still")
eq(ServerAddress.with(ServerAddress.url(base, "api/x.jpg")!, "h", "480").absoluteString, "https://home.garito.ca/api/x.jpg?h=480", "query")
eq(ServerAddress.wallURL(base)?.absoluteString, "https://home.garito.ca/wall", "wall url")
check(ServerAddress.isPrivateHost("172.20.0.1") && !ServerAddress.isPrivateHost("172.32.0.1") && !ServerAddress.isPrivateHost("8.8.8.8"), "private ranges")

// MARK: connecting to a hub (GET /api/hello, newman://connect, Bonjour, home/outside)

eq(ServerAddress.lanURL(host: "10.1.1.2", port: 8093)?.absoluteString, "http://10.1.1.2:8093/", "bonjour v4")
eq(ServerAddress.lanURL(host: "10.1.1.2%en0", port: 8093)?.absoluteString, "http://10.1.1.2:8093/", "bonjour interface suffix")
check(ServerAddress.lanURL(host: "fe80::1%en0", port: 8093) != nil, "bonjour v6")
let hello = HubHello.parse(Data(#"{"app":"newman","id":"h1","name":"Home","version":"1.2","outsideUrl":"https://home.example.com","localUrls":["http://10.1.1.2:8093"]}"#.utf8))
eq(hello?.id, "h1", "hello id")
check(HubHello.parse(Data(#"{"app":"other","id":"x"}"#.utf8)) == nil, "hello: not newman")
check(HubHello.parse(Data(#"<html>hi</html>"#.utf8)) == nil, "hello: html")
let qrLink = ConnectLink.parse("newman://connect?v=1&id=h1&name=My%20Home&local=http%3A%2F%2F10.1.1.2%3A8093&outside=")
eq(qrLink?.id, "h1", "link id"); eq(qrLink?.name, "My Home", "link name")
eq(qrLink?.local?.absoluteString, "http://10.1.1.2:8093/", "link local"); check(qrLink?.outside == nil, "link empty outside")
eq(ConnectLink.parse("newman://connect?id=h1&name=My+Home&outside=https%3A%2F%2Fhome.example.com")?.name, "My Home", "link + name")
check(ConnectLink.parse("newman://connect?id=h1") == nil, "link without addresses")
check(ConnectLink.parse("https://home.example.com") == nil, "not a link")
if let hello {
    let viaLocal = HubPlan.saved(tested: ServerAddress.normalize("10.1.1.2:8093")!, hello: hello, candidate: .typed(ServerAddress.normalize("10.1.1.2:8093")!))
    eq(viaLocal.localURL?.absoluteString, "http://10.1.1.2:8093/", "typed local")
    eq(viaLocal.outsideURL?.absoluteString, "https://home.example.com/", "outside from hello")
    let viaOutside = HubPlan.saved(tested: ServerAddress.normalize("home.example.com")!, hello: hello, candidate: .typed(ServerAddress.normalize("home.example.com")!))
    eq(viaOutside.localURL?.absoluteString, "http://10.1.1.2:8093/", "local from hello")
    eq(viaOutside.name, "Home", "hub name")
    eq(HubPlan.route(viaOutside, localAnswers: true)?.absoluteString, "http://10.1.1.2:8093/", "at home")
    eq(HubPlan.route(viaOutside, localAnswers: false)?.absoluteString, "https://home.example.com/", "away")
    check(HubPlan.isSameHub(oldHub: nil, oldAddress: ServerAddress.normalize("home.example.com"), newHub: viaLocal,
                            newAddress: viaLocal.localURL!), "older install, same hub")
    check(!HubPlan.isSameHub(oldHub: SavedHub(id: "other", name: "", localURL: nil, outsideURL: base), oldAddress: base,
                             newHub: viaLocal, newAddress: viaLocal.localURL!), "another hub")
}
eq(HubCheck.reply(status: 200, finalHost: "team.cloudflareaccess.com", body: Data(), hasCloudflare: false, hasCertificate: false),
   .failed(HubCheck.needsCloudflare), "cloudflare redirect")
eq(HubCheck.reply(status: 200, finalHost: "example.com", body: Data("<html></html>".utf8), hasCloudflare: false, hasCertificate: false),
   .failed(HubCheck.notNewman), "not newman")
eq(HubCheck.reply(status: 400, finalHost: nil, body: Data("No required SSL certificate was sent".utf8), hasCloudflare: false, hasCertificate: false),
   .failed(HubCheck.needsCertificate), "nginx mTLS")
eq(HubCheck.network(code: URLError.clientCertificateRequired.rawValue, hasCertificate: false), HubCheck.needsCertificate, "mTLS handshake")
eq(HubCheck.network(code: URLError.cannotConnectToHost.rawValue, hasCertificate: false), HubCheck.unreachable, "unreachable")
eq(JSONBody.errorText(Data(#"{"ok": false, "error": "Lock jammed"}"#.utf8)), "Lock jammed", "error text")
check(JSONBody.isSignInRequired(status: 401, data: Data(#"{"error": "Please sign in", "signIn": true}"#.utf8)), "sign-in required")

// MARK: home / outside address on the phone (HubRoutePolicy; the wall never switches)

do {
    let net = "wifi-en0,10.1.1.1"
    // A hub restart at home: home misses probes for a minute, then answers - the phone stays home.
    var p = HubRoutePolicy(using: .home, homeNetwork: net)
    eq(p.home(answered: false, now: 1000, network: net), .stay, "restart: first miss stays home")
    eq(p.nextProbe, HubRoutePolicy.failingRecheckSeconds, "failing home is re-probed")
    eq(p.home(answered: false, now: 1060, network: net), .stay, "restart: 60 s still home")
    eq(p.home(answered: false, now: 1119, network: net), .stay, "restart: 119 s still home")
    eq(p.home(answered: true, now: 1125, network: net), .stay, "restart: home back")
    eq(p.using, .home, "restart: never left home")
    eq(p.nextProbe, nil, "healthy home: no timer")
    // Home failing for 2 minutes on the same network: ask outside; switch only if it's the same hub.
    eq(p.home(answered: false, now: 2000, network: net), .stay, "outage: first miss")
    eq(p.home(answered: false, now: 2060, network: net), .stay, "outage: 60 s")
    eq(p.home(answered: false, now: 2120, network: net), .probeOutside, "outage: 2 minutes -> try outside")
    check(!p.outside(sameHub: false), "outside not answering as this hub: stay")
    eq(p.using, .home, "still home")
    eq(p.home(answered: false, now: 2135, network: net), .probeOutside, "outage: keeps trying outside")
    check(p.outside(sameHub: true), "outside answers as this hub: switch")
    eq(p.using, .outside, "now outside")
    eq(p.nextProbe, HubRoutePolicy.awayRecheckSeconds, "outside: home re-probed every 30 s")
    eq(p.home(answered: false, now: 2165, network: net), .stay, "outside, home still down")
    eq(p.home(answered: true, now: 2195, network: net), .useHome, "home answers: back home at once")
    eq(p.using, .home, "home again")
    // A failure long after the previous one (the app was in the background) restarts the clock.
    var g = HubRoutePolicy(using: .home, homeNetwork: net)
    _ = g.home(answered: false, now: 0, network: net)
    eq(g.home(answered: false, now: 3600, network: net), .stay, "gap: the 2 minutes start again")
    eq(g.home(answered: false, now: 3700, network: net), .stay, "gap: 100 s")
    eq(g.home(answered: false, now: 3720, network: net), .probeOutside, "gap: 120 s")
    // The network changed (Wi-Fi -> cellular, another Wi-Fi): no need to wait.
    var c = HubRoutePolicy(using: .home, homeNetwork: net)
    c.pathChanged()
    eq(c.home(answered: false, now: 50, network: net), .probeOutside, "path change: try outside at once")
    check(c.outside(sameHub: true), "path change: switch")
    var s = HubRoutePolicy(using: .home, homeNetwork: net)
    eq(s.home(answered: false, now: 50, network: "wifi-en0,192.168.0.1"), .probeOutside, "another Wi-Fi: try outside")
    var cell = HubRoutePolicy(using: .home)
    eq(cell.home(answered: false, now: 50, network: "cellular-pdp_ip0"), .probeOutside, "on cellular: try outside")
    var unknown = HubRoutePolicy(using: .home)
    eq(unknown.home(answered: false, now: 50, network: net), .stay, "unknown home network on Wi-Fi: wait")
    var learnt = HubRoutePolicy(using: .home)
    _ = learnt.home(answered: true, now: 10, network: net)
    eq(learnt.homeNetwork, net, "home network learnt")
    var away = HubRoutePolicy(using: .outside)
    eq(away.home(answered: false, now: 0, network: "cellular-pdp_ip0"), .stay, "outside: home down, stay")
    check(!away.outside(sameHub: true), "already outside: nothing to switch")
}

// MARK: widgets, controls, Siri (WidgetLogic: kind metadata only)

do {
    let shown = entities.filter { WidgetLogic.isShowable($0, kinds[$0.kind]) }
    check(!shown.contains { kinds[$0.kind]?.hasCamera == true }, "widgets: no cameras")
    check(!shown.contains { DeviceLogic.isRemote($0, kinds[$0.kind]) }, "widgets: no remotes")
    for want in ["light", "lock", "garage", "temperature", "contact", "thermostat"] {
        check(shown.contains { $0.kind == want }, "widgets: shows \(want)")
    }
    let secs = WidgetLogic.sections(entities, kinds: kinds)
    check(secs.flatMap(\.ids).count == shown.count, "sections hold every showable device once")
    let roomTitles = Set(entities.compactMap { WidgetLogic.isShowable($0, kinds[$0.kind]) ? $0.area : nil })
    eq(Array(secs.prefix(roomTitles.count)).map(\.title), roomTitles.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending },
       "rooms first, A-Z")
    check(secs.contains { $0.title == (kinds["lock"]?.plural ?? kinds["lock"]?.name ?? "") }, "no room: by type")
    if let kitchen = byId["lutron.device_8"] {
        let d = WidgetLogic.device(kitchen, kinds["light"])
        eq(WidgetLogic.tapAction(d, kinds["light"])?.command, "turn_off", "light on: tap turns off")
        eq(WidgetLogic.tapAction(d, nil)?.command, "turn_off", "hub's first action without kinds")
        check(WidgetLogic.isLit(d, kinds["light"]), "light on is lit")
        check(WidgetLogic.isOn(d, kinds["light"]), "light on is on")
        eq(WidgetLogic.detail(kitchen, kinds["light"]), "Kitchen · \(kinds["light"]?.name ?? "")", "picker detail")
        check(WidgetLogic.matches(kitchen, kinds["light"], query: "kitch"), "search by room")
    } else { check(false, "kitchen light fixture") }
    if let lock = entities.first(where: { $0.kind == "lock" && $0.state == "LOCKED" }) {
        let d = WidgetLogic.device(lock, kinds["lock"])
        let a = WidgetLogic.tapAction(d, kinds["lock"])
        eq(a?.command, "unlock", "locked: tap unlocks")
        check(a.map { WidgetLogic.isSensitive($0, kinds["lock"]) } ?? false, "unlock is sensitive (from meta)")
        check(!WidgetLogic.isWarning(d, kinds["lock"]), "locked: no warning")
        var open = d
        open.state = "UNLOCKED"
        check(WidgetLogic.isWarning(open, kinds["lock"]), "unlocked: warning")
        check(!WidgetLogic.isLit(open, kinds["lock"]), "a lock never lights up")
    } else { check(false, "lock fixture") }
    check(WidgetLogic.toggle(kinds["light"]) != nil, "light: Control Center switch")
    eq(WidgetLogic.toggle(kinds["light"])?.on.id, "turn_on", "switch on")
    eq(WidgetLogic.toggle(kinds["light"])?.off.id, "turn_off", "switch off")
    check(WidgetLogic.toggle(kinds["lock"]) == nil, "lock: a button, not a switch (unlock is sensitive)")
    check(WidgetLogic.toggle(kinds["garage"]) == nil, "garage: a button")
    check(WidgetLogic.toggle(kinds["temperature"]) == nil, "sensor: no switch")
    eq(WidgetLogic.command(.turnOn, kinds["light"])?.id, "turn_on", "siri turn on")
    eq(WidgetLogic.command(.turnOff, kinds["switch"])?.id, "turn_off", "siri turn off")
    eq(WidgetLogic.command(.open, kinds["garage"])?.id, "open", "siri open garage")
    eq(WidgetLogic.command(.close, kinds["garage"])?.id, "close", "siri close garage")
    eq(WidgetLogic.command(.lock, kinds["lock"])?.id, "lock", "siri lock")
    eq(WidgetLogic.command(.unlock, kinds["lock"])?.id, "unlock", "siri unlock")
    eq(WidgetLogic.command(.turnOn, kinds["siren"])?.id, "turn_on", "siri: asked by name, sensitive allowed")
    // Sensitive commands need an unlocked phone (meta / hub flag, never a list of kinds here).
    check(WidgetLogic.isSensitive(command: "turn_on", nil, kinds["siren"]), "siren on: sensitive (from meta)")
    check(WidgetLogic.isSensitive(command: "unlock", nil, kinds["lock"]), "unlock: sensitive")
    check(!WidgetLogic.isSensitive(command: "turn_on", nil, kinds["light"]), "light on: not sensitive")
    check(!WidgetLogic.isSensitive(command: "lock", nil, kinds["lock"]), "lock: not sensitive")
    let flagged = WidgetDevice(id: "x", name: "X", available: true,
                               actions: [WidgetAction(command: "open", label: "Open", sensitive: true)])
    check(WidgetLogic.isSensitive(command: "open", flagged, nil), "hub's sensitive flag counts without kinds")
    check(!WidgetLogic.isSensitive(command: "close", flagged, nil), "other commands stay plain")
    check(WidgetLogic.mustUnlockFirst(sensitive: true, intentAuthenticates: false, phoneUnlocked: false), "sensitive, locked: refuse")
    check(!WidgetLogic.mustUnlockFirst(sensitive: true, intentAuthenticates: false, phoneUnlocked: true), "sensitive, unlocked: runs")
    check(!WidgetLogic.mustUnlockFirst(sensitive: true, intentAuthenticates: true, phoneUnlocked: false), "iOS already authenticated")
    check(!WidgetLogic.mustUnlockFirst(sensitive: false, intentAuthenticates: false, phoneUnlocked: false), "not sensitive: runs locked")
    check(WidgetLogic.command(.turnOn, kinds["temperature"]) == nil, "a sensor can't be turned on")
    check(WidgetLogic.command(.open, kinds["light"]) == nil, "a light doesn't open")
    if let t = entities.first(where: { $0.kind == "temperature" }) {
        let d = WidgetLogic.device(t, kinds["temperature"])
        check(d.actionList.isEmpty && WidgetLogic.tapAction(d, kinds["temperature"]) == nil, "sensor: not a button")
        check(d.reading != nil && WidgetLogic.stateText(d) == d.reading?.text && !WidgetLogic.stateText(d).isEmpty, "sensor shows its reading (\(WidgetLogic.stateText(d)))")
    }
    // GET /api/widgets/state, tolerant of missing fields and of a device it can't read.
    let stateJSON = #"""
    {"devices":[
     {"id":"lutron.device_8","name":"Kitchen","kind":"light","state":"on","stateLabel":"On","available":true,"icon":"bulb",
      "area":"Kitchen","actions":[{"command":"turn_off","label":"Turn off","sensitive":false}],"reading":null},
     {"id":"homekit.temp_1","name":"Hall","kind":"temperature","state":22.5,"reading":{"value":22.5,"unit":"°C"}},
     {"name":"no id"},
     {"id":"bare"}]}
    """#
    let ds = WidgetStateReply.parse(Data(stateJSON.utf8)) ?? []
    eq(ds.map(\.id), ["lutron.device_8", "homekit.temp_1", "bare"], "widget state: unreadable device skipped")
    eq(ds.first?.actionList.first?.command, "turn_off", "widget actions")
    eq(ds.count > 1 ? WidgetLogic.stateText(ds[1]) : "", "22.5 °C", "reading text")
    eq(ds.count > 1 ? ds[1].state : nil, "22.5", "numeric state as text")
    eq(ds.last?.name, "bare", "name falls back to the id")
    check(ds.last.map { WidgetLogic.tapAction($0, nil) == nil } ?? false, "no actions: not a button")
    eq(WidgetReading(value: .number(64), unit: "%").text, "64%", "percent reading")
    var off = ds[0]
    off.available = false
    eq(WidgetLogic.stateText(off), "Offline", "offline")
    check(WidgetLogic.tapAction(off, kinds["light"]) == nil, "offline: no action")
    eq(WidgetLogic.ordered(ds, ids: ["bare", "missing", "lutron.device_8"]).map(\.id), ["bare", "lutron.device_8"], "widget order")
    let tb = WidgetLogic.pushTokenBody(token: "ab01", deviceId: "ios-1", sandbox: true, appBundleId: "ca.nimaste.newman")
    eq(tb["kind"], .string("ios-widget"), "push token kind"); eq(tb["token"], .string("ab01"), "push token")
    eq(tb["deviceId"], .string("ios-1"), "push token device"); eq(tb["topic"], .string("ca.nimaste.newman.push-type.widgets"), "topic")
    eq(tb["apnsEnvironment"], .string("sandbox"), "push token sandbox")
    check(WidgetLogic.pushTokenBody(token: "a", deviceId: "d", sandbox: false, appBundleId: "x")["apnsEnvironment"] == nil, "production")
    let st1 = WidgetLogic.registrationStamp(token: "t", server: "https://h/", session: "s1")
    eq(st1, WidgetLogic.registrationStamp(token: "t", server: "https://h/", session: "s1"), "stamp stable")
    check(st1 != WidgetLogic.registrationStamp(token: "t", server: "https://h/", session: "s2"), "new session: re-send")
    check(!st1.contains("s1"), "stamp doesn't hold the session")
    eq(WidgetLogic.nextRefresh(now: 1000, lastActionAt: 950), 1030, "just acted: soon")
    eq(WidgetLogic.nextRefresh(now: 1000, lastActionAt: nil), 1900, "else 15 minutes")
}

// MARK: device widget layouts, tones and Home status (GET /api/widgets/home)

do {
    eq([WidgetSize.small, .medium, .large].map { WidgetLogic.maxDevices($0) }, [2, 4, 8], "devices per size")
    // Small (square): 1 fills it, 2 stack. Medium (wide): side by side. Large: up to 3 stacked, then 2 across.
    eq(WidgetLogic.lines(count: 1, columns: WidgetLogic.tileColumns(count: 1, wide: false)), [1], "small 1")
    eq(WidgetLogic.lines(count: 2, columns: WidgetLogic.tileColumns(count: 2, wide: false)), [1, 1], "small 2: halves")
    eq(WidgetLogic.lines(count: 3, columns: WidgetLogic.tileColumns(count: 3, wide: true)), [3], "medium 3 in a row")
    eq(WidgetLogic.lines(count: 4, columns: WidgetLogic.tileColumns(count: 4, wide: true)), [4], "medium 4 in a row")
    eq(WidgetLogic.lines(count: 3, columns: WidgetLogic.tileColumns(count: 3, wide: false)), [1, 1, 1], "large 3 stacked")
    eq(WidgetLogic.lines(count: 5, columns: WidgetLogic.tileColumns(count: 5, wide: false)), [2, 2, 1], "large 5: last widens")
    eq(WidgetLogic.lines(count: 8, columns: WidgetLogic.tileColumns(count: 8, wide: false)), [2, 2, 2, 2], "large 8")
    eq(WidgetLogic.lines(count: 0, columns: 2), [], "nothing")
    check(WidgetLogic.tileCompact(tileHeight: 70) && !WidgetLogic.tileCompact(tileHeight: 150), "short tiles go sideways")
    check(WidgetLogic.rowStacked(rowHeight: 70) && !WidgetLogic.rowStacked(rowHeight: 36), "tall rows stack the state")
    eq(WidgetLogic.deviceURL("lutron.device 8")?.absoluteString, "newman://device?id=lutron.device%208", "device link")

    // Tones: the hub's when it sends one, else from the kind metadata (secure state, then active state).
    let locked = WidgetDevice(id: "l", name: "Side Door", kind: "lock", state: "LOCKED", available: true)
    eq(WidgetLogic.tone(locked, kinds["lock"]), .good, "locked: good")
    var unlocked = locked
    unlocked.state = "UNLOCKED"
    eq(WidgetLogic.tone(unlocked, kinds["lock"]), .active, "unlocked: active")
    unlocked.state = "UNLOCKING"
    eq(WidgetLogic.tone(unlocked, kinds["lock"]), .neutral, "between states: neutral")
    let on = WidgetDevice(id: "x", name: "Kitchen", kind: "light", state: "on")
    eq(WidgetLogic.tone(on, kinds["light"]), .active, "light on: active")
    eq(WidgetLogic.tone(WidgetDevice(id: "x", name: "K", kind: "light", state: "off"), kinds["light"]), .neutral, "off: neutral")
    if kinds["leak"]?.secureState != nil {
        eq(WidgetLogic.tone(WidgetDevice(id: "w", name: "Laundry", kind: "leak", state: "wet"), kinds["leak"]), .alert, "leak: alert")
    }
    eq(WidgetLogic.tone(WidgetDevice(id: "x", name: "K", available: false), nil), .offline, "offline")
    eq(WidgetLogic.tone(WidgetDevice(id: "x", name: "K", tone: "good"), nil), .good, "hub's tone")
    eq(WidgetLogic.tone(WidgetDevice(id: "x", name: "K", kind: "light", state: "on", tone: ""), kinds["light"]), .neutral,
       "hub's neutral wins")
    let toned = WidgetStateReply.parse(Data(#"{"devices":[{"id":"a","name":"A","tone":"alert"}]}"#.utf8))
    eq(toned?.first?.tone, "alert", "tone decoded")

    // GET /api/widgets/home (server/tests/test_widgets.py has the hub side).
    let homeJSON = #"""
    {"secure": false, "headline": "Side Door unlocked", "detail": "door closed",
     "problems": [{"id": "fake.side", "name": "Side Door", "kind": "lock", "icon": "lock", "state": "UNLOCKED",
                   "stateLabel": "Unlocked", "urgent": false, "fix": {"command": "lock", "label": "Lock", "sensitive": false}},
                  {"id": "fake.leak", "name": "Laundry", "kind": "leak", "icon": "leak", "state": "wet",
                   "stateLabel": "Leak!", "urgent": true, "fix": null}],
     "groups": [{"kind": "lock", "label": "Lock", "text": "Unlocked", "secure": false, "icon": "lock"},
                {"kind": "alarm", "label": "Alarm", "text": "Disarmed", "secure": null, "icon": "alarm"}],
     "ids": ["fake.side", "fake.leak", "fake.alarm"], "somethingNew": 1}
    """#
    if let home = WidgetHome.parse(Data(homeJSON.utf8)) {
        eq(home.secure, false, "home: not secure")
        eq(home.headline, "Side Door unlocked", "home headline")
        eq(home.fixable.map(\.id), ["fake.side"], "only fixable problems get buttons")
        eq(home.fixable.first?.fix?.command, "lock", "fix command")
        eq(home.fixable.first?.device.name, "Side Door", "fix device")
        eq(home.groupList.map(\.text), ["Unlocked", "Disarmed"], "home groups")
        check(home.groupList[1].secure == nil, "a house mode isn't secure or not")
    } else { check(false, "home status decodes") }
    let bare = WidgetHome.parse(Data(#"{"secure": null, "headline": "Nothing to keep secure"}"#.utf8))
    check(bare != nil && bare?.secure == nil && bare?.problemList.isEmpty == true && bare?.groupList.isEmpty == true,
          "home status: missing lists are empty")
}

// MARK: live video (mobile-shared/live-player.html protocol)

eq(LivePageMessage.parse(["type": "ready"]), .ready, "page ready")
eq(LivePageMessage.parse(["type": "codecs", "value": "avc1.640029,mp4a.40.2"]), .codecs("avc1.640029,mp4a.40.2"), "codecs")
eq(LivePageMessage.parse(["type": "state", "value": "playing"]), .state(.playing), "playing")
eq(LivePageMessage.parse(["type": "state", "value": "failed"]), .state(.failed), "failed")
eq(LivePageMessage.parse(["type": "state", "value": "retry"]), .state(.retry), "retry")
eq(LivePageMessage.parse("nonsense"), .unknown, "not a dict")
eq(LiveScript.mseRequest(codecs: "avc1.640029,flac"), #"{"type":"mse","value":"avc1.640029,flac"}"#, "mse request")
eq(LiveScript.text(#"{"type":"mse","value":"video/mp4; codecs=\"avc1.640029\""}"#),
   #"NewmanLive.text("{\"type\":\"mse\",\"value\":\"video/mp4; codecs=\\\"avc1.640029\\\"\"}")"#, "text literal")
eq(LiveScript.text("a\u{2028}b</script>\n"), #"NewmanLive.text("a\u2028b\u003c/script>\n")"#, "js-safe literal")
eq(LiveScript.data(Data([0, 1, 2, 250])), #"NewmanLive.data("AAEC+g==")"#, "base64 data")
// The literal must round-trip through a JSON parser to the same text.
let tricky = "quote \" back \\ tab \t nl \n ctrl \u{01} emoji 🎥"
let lit = LiveScript.jsStringLiteral(tricky)
eq(try? JSONDecoder().decode(String.self, from: Data(lit.utf8)), tricky, "literal round-trips")
eq(LiveScript.socketURL(base: ServerAddress.normalize("https://home.garito.ca")!, livePath: "api/entities/frigate.camera_garage/live", quality: .sub)?.absoluteString,
   "wss://home.garito.ca/api/entities/frigate.camera_garage/live?quality=sub", "wss live url")
eq(LiveScript.socketURL(base: ServerAddress.normalize("http://10.1.1.2:8093")!, livePath: "api/entities/x/live", quality: .main)?.absoluteString,
   "ws://10.1.1.2:8093/api/entities/x/live?quality=main", "ws live url")
check(entities.contains { $0.live != nil }, "fixture cameras have live paths")

let pool = StreamPool(capacity: 4)
var granted: [String] = []
for i in 1...6 { if pool.request("t\(i)", grant: { granted.append("t\(i)") }) { granted.append("t\(i)") } }
eq(granted, ["t1", "t2", "t3", "t4"], "pool grants 4")
eq(pool.waitingIds, ["t5", "t6"], "two wait in order")
pool.release("t2")
eq(granted.last, "t5", "a freed slot goes to the first waiter")
eq(pool.active.sorted(), ["t1", "t3", "t4", "t5"], "active after release")
pool.release("t6")
eq(pool.waitingIds, [], "a waiter can leave")
pool.release("t1"); pool.release("t3")
eq(pool.active.count, 2, "releasing below capacity grants nobody new")
check(pool.request("t4", grant: {}), "asking again while active is fine")

// MARK: wall tablet battery (POST /api/tablet/battery)

eq(TabletBattery.fromIOS(level: 0.87, stateRaw: 2), TabletBattery(level: 87, charging: true, plugged: "ac", temperatureC: nil, voltageMv: nil), "charging")
eq(TabletBattery.fromIOS(level: 1, stateRaw: 3)?.charging, true, "full counts as charging")
eq(TabletBattery.fromIOS(level: 0.42, stateRaw: 1), TabletBattery(level: 42, charging: false, plugged: "none", temperatureC: nil, voltageMv: nil), "unplugged")
eq(TabletBattery.fromIOS(level: 0.5, stateRaw: 0)?.plugged, "none", "unknown state")
check(TabletBattery.fromIOS(level: -1, stateRaw: 0) == nil, "unknown level is skipped")
let batteryBody = (try? JSONSerialization.jsonObject(with: TabletBattery(level: 87, charging: true, plugged: "ac", temperatureC: nil, voltageMv: nil).json)) as? [String: Any] ?? [:]
eq(Set(batteryBody.keys), ["level", "charging", "plugged", "temperatureC", "voltageMv"], "battery body keys")
eq((batteryBody["level"] as? NSNumber)?.intValue, 87, "battery level")
eq(batteryBody["plugged"] as? String, "ac", "battery plugged")
check(batteryBody["temperatureC"] is NSNull && batteryBody["voltageMv"] is NSNull, "unknown values are null")
let batteryText = String(decoding: TabletBattery(level: 5, charging: false, plugged: "none", temperatureC: 31.5, voltageMv: 4012).json, as: UTF8.self)
check(batteryText.contains(#""charging":false"#) && batteryText.contains(#""voltageMv":4012"#) && batteryText.contains(#""temperatureC":31.5"#), "battery json \(batteryText)")
check(TabletBattery.cookieDomainMatches("home.garito.ca", host: "Home.Garito.ca"), "host cookie")
check(TabletBattery.cookieDomainMatches(".garito.ca", host: "home.garito.ca"), "domain cookie")
check(TabletBattery.cookieDomainMatches(".garito.ca", host: "garito.ca"), "domain cookie, bare")
check(!TabletBattery.cookieDomainMatches("garito.ca", host: "home.garito.ca"), "host cookie is host-only")
check(!TabletBattery.cookieDomainMatches(".evilgarito.ca", host: "home.garito.ca"), "other domain")
check(!TabletBattery.cookieDomainMatches("10.1.1.2", host: "10.1.1.20"), "other ip")

func battery(_ level: Int, _ charging: Bool = false) -> TabletBattery {
    TabletBattery(level: level, charging: charging, plugged: charging ? "ac" : "none", temperatureC: nil, voltageMv: nil)
}
let sched = BatterySchedule()
check(sched.shouldSend(now: 0, battery(80)), "first report")
sched.started()
check(!sched.shouldSend(now: 1, battery(80)), "one at a time")
sched.succeeded(now: 1, battery(80))
check(!sched.shouldSend(now: 120, battery(80)), "nothing new")
check(!sched.shouldSend(now: 120, battery(76)), "4 points is not enough")
check(sched.shouldSend(now: 120, battery(75)), "5 points down")
check(sched.shouldSend(now: 120, battery(85)), "5 points up")
check(sched.shouldSend(now: 120, battery(80, true)), "charging started")
check(sched.shouldSend(now: 1 + 300 - 5, battery(80)), "five minutes (timer a little early)")
check(!sched.shouldSend(now: 241, battery(80)), "four minutes")
sched.started()
sched.skipped()
check(sched.shouldSend(now: 300, battery(80)), "not signed in: no back-off")
sched.started()
sched.failed(now: 300)
check(!sched.shouldSend(now: 360, battery(40, true)), "after a failure, wait an interval")
sched.forceNext()
check(sched.shouldSend(now: 361, battery(40, true)), "back in front: at once, even after a failure")
let offBody = (try? JSONSerialization.jsonObject(with: battery(60).with(screenOn: false).json)) as? [String: Any] ?? [:]
eq(offBody["screen"] as? String, "off", "screen off is sent")
check(offBody["background"] == nil, "no background reports from an iPad")
eq(((try? JSONSerialization.jsonObject(with: battery(60).with(screenOn: true).json)) as? [String: Any])?["screen"] as? String, "on", "screen on")
check(((try? JSONSerialization.jsonObject(with: battery(60).json)) as? [String: Any])?["screen"] == nil, "no screen unless set")
check(sched.shouldSend(now: 600, battery(80)), "then try again")

// MARK: people & location (GET /api/people, /api/me/location, report scheduling)

let peopleJSON = #"""
[{"id":"p1","name":"Chris","sharing":true,"home":true,"place":null,"distanceM":null,"lat":43.6,"lon":-79.4,"accuracyM":12,"at":1790000000.5,"color":"#3ccfb4","photo":"/api/people/p1/photo?v=ab12"},
 {"id":"p2","name":"Dante","sharing":true,"home":false,"place":"School","distanceM":2100,"lat":43.61,"lon":-79.41,"accuracyM":30,"at":1789999700,"live":true,"color":"#FF8A6B","photo":null},
 {"id":"p3","name":"Massimo","sharing":false,"home":false,"place":null,"distanceM":null,"lat":null,"lon":null,"accuracyM":null,"at":null}]
"""#
let household = (try? JSONDecoder().decode([Person].self, from: Data(peopleJSON.utf8))) ?? []
eq(household.count, 3, "people decode")
eq(household.first?.hasFix, true, "fix")
eq(household.last?.hasFix, false, "no fix")
eq(household.first?.color, "#3ccfb4", "colour decodes")
eq(PersonLook.photoPath(household.first), "/api/people/p1/photo?v=ab12", "photo path")
eq(PersonLook.photoPath(household[1]), nil, "no photo")
eq(PersonLook.rgb(household[0], index: 0), 0x3CCFB4, "own colour")
eq(PersonLook.rgb(household[2], index: 1), 0xC58BFF, "older hub: colour by position")
eq(PersonLook.rgb(Person(id: "x", color: "nonsense"), index: 7), 0xC58BFF, "bad colour falls back")
eq(PersonLook.rgb("#12345"), nil, "five digits isn't a colour")
eq(PersonLook.rgb("+12345"), nil, "a sign isn't a colour")
eq(PersonLook.normalized("3ccfb4"), "#3CCFB4", "normalized")
eq(PersonLook.normalized("#00000a"), "#00000A", "leading zeros kept")
check(PersonLook.same("#3ccfb4", "#3CCFB4") && !PersonLook.same("#3ccfb4", nil), "same colour")
eq(PersonLook.presets.count, 12, "twelve swatches")
check(PersonLook.presets.allSatisfy { PersonLook.rgb($0) != nil }, "swatches are colours")
eq(PersonLook.profileBody(color: "#ff7eb6")["color"], .string("#FF7EB6"), "profile body")
let fit = PersonLook.fitted(width: 4032, height: 3024)
check(fit.width == 1024 && fit.height == 768, "downscaled \(fit)")
let small = PersonLook.fitted(width: 800, height: 600)
check(small.width == 800 && small.height == 600, "small stays")
let crop = PersonLook.squareCrop(width: 1024, height: 768)
check(crop.x == 128 && crop.y == 0 && crop.side == 768, "square crop \(crop)")
if case .person(let p)? = LiveMessage.parse(##"{"type":"person","person":{"id":"p2","name":"Dante","sharing":true,"home":true,"at":1790000100,"color":"#C58BFF","photo":"/api/people/p2/photo?v=9"}}"##) {
    let updated = PeopleList.upsert(household, p)
    eq(updated.map { $0.id }, ["p1", "p2", "p3"], "person replaced in place")
    eq(updated[1].home, true, "person message applied")
    eq(updated[1].photo, "/api/people/p2/photo?v=9", "person message carries the photo")
    eq(PeopleList.upsert(household, Person(id: "p4", name: "Nicholas")).count, 4, "new person goes last")
} else { check(false, "person message parses") }

eq(PeopleText.distance(846), "850 m", "metres")
eq(PeopleText.distance(3), "10 m", "tiny distance")
eq(PeopleText.distance(999.9), "1 km", "round up to a km")
eq(PeopleText.distance(3240), "3.2 km", "km with a decimal")
eq(PeopleText.distance(3990), "4 km", "whole km")
eq(PeopleText.distance(12_400), "12 km", "far")
eq(PeopleText.whereText(Person(id: "a", sharing: true, home: true, place: "School")), "Home", "home wins")
eq(PeopleText.whereText(Person(id: "a", sharing: true, place: "School", distanceM: 2000)), "School", "named place")
eq(PeopleText.whereText(Person(id: "a", sharing: true, distanceM: 3200)), "3.2 km away", "distance")
eq(PeopleText.whereText(Person(id: "a", sharing: false, home: true)), "Not sharing location", "not sharing")
eq(PeopleText.whereText(Person(id: "a", sharing: true)), "No location yet", "no fix yet")
let t0 = 1_790_000_000.0
eq(PeopleText.updated(t0 - 20, now: t0), "updated just now", "just now")
eq(PeopleText.updated(t0 - 270, now: t0), "updated 4 min ago", "minutes")
eq(PeopleText.updated(t0 - 7300, now: t0), "updated 2 h ago", "hours")
eq(PeopleText.updated(t0 - 30 * 3600, now: t0), "updated yesterday", "yesterday")
eq(PeopleText.updated(t0 - 3 * 86400 - 5, now: t0), "updated 3 days ago", "days")
eq(PeopleText.updated(nil, now: t0), nil, "never")
let marlina = Person(id: "m", name: "Marlina", sharing: true, distanceM: 3200, lat: 1, lon: 2, at: t0 - 240)
eq(PeopleText.row(marlina, now: t0).updated, "updated 4 min ago", "row updated")
eq(PeopleText.mapLine(marlina, now: t0), "3.2 km away · updated 4 min ago", "map line")
eq(PeopleText.row(Person(id: "x", name: "Massimo", sharing: false, at: t0), now: t0).updated, nil, "no time when not sharing")
eq(PeopleText.initial(" massimo"), "M", "initial")

eq(SharingSwitch.state(nil).visible, false, "older hub: no switch")
eq(SharingSwitch.state(LocationSettings(mode: "off")).visible, false, "off: hidden")
let choiceOff = SharingSwitch.state(LocationSettings(mode: "choice", enabled: false))
check(choiceOff.visible && !choiceOff.on && !choiceOff.locked, "choice off")
let choiceOn = SharingSwitch.state(LocationSettings(mode: "choice", enabled: true))
check(choiceOn.on && !choiceOn.locked, "choice on")
let required = SharingSwitch.state(LocationSettings(mode: "required", enabled: true, requiredBy: "Chris"))
check(required.visible && required.on && required.locked, "required is on and locked")
eq(required.note, "Required by Chris", "required by")
eq(SharingSwitch.state(LocationSettings(mode: "required")).note, "Required by your household", "required, nobody named")
check(SharingSwitch.permissionReason(LocationSettings(mode: "required")).hasPrefix("Your household requires location sharing"), "required wording")
check(LocationSettings(mode: "required", enabled: false).sharing, "required shares")
check(!LocationSettings(mode: "off", enabled: true).sharing, "off never shares")
let settingsJSON = #"{"mode":"required","enabled":true,"requiredBy":"Chris","everySeconds":300,"live":false}"#
// MARK: tap-to-refresh (POST /api/people/<id>/refresh)
let dante = household[1]
var rs = PersonRefresh.start(.idle, person: dante, now: t0)
eq(rs.phase, .updating, "tap: updating")
eq(PersonRefresh.text(rs, now: t0), "Updating…", "updating words")
eq(PersonRefresh.start(rs, person: dante, now: t0 + 3), rs, "a second tap while updating changes nothing")
rs = PersonRefresh.replied(rs, status: 202, nextAllowedAt: t0 + 120, now: t0 + 1)
eq(rs.phase, .updating, "202 keeps waiting")
eq(PersonRefresh.personChanged(rs, Person(id: "p2", at: dante.at)).phase, .updating, "same fix: still updating")
eq(PersonRefresh.personChanged(rs, Person(id: "p2", at: nil)).phase, .updating, "no fix: still updating")
eq(PersonRefresh.personChanged(rs, Person(id: "p2", at: (dante.at ?? 0) + 200)), .idle, "newer fix: done")
eq(PersonRefresh.tick(rs, now: t0 + 30), rs, "still waiting at 30 s")
let timedOut = PersonRefresh.tick(rs, now: t0 + 45)
eq(timedOut.phase, .failed, "45 s: gave up")
eq(PersonRefresh.text(timedOut, now: t0 + 46), "Couldn't reach their phone right now", "timeout words")
eq(PersonRefresh.tick(timedOut, now: t0 + 45 + 8), .idle, "the message goes away")
let tooSoon = PersonRefresh.replied(PersonRefresh.start(.idle, person: dante, now: t0), status: 429, nextAllowedAt: t0 + 74.2, now: t0)
eq(tooSoon.phase, .tooSoon, "429")
eq(PersonRefresh.text(tooSoon, now: t0), "Updated recently - try again in 75 s", "429 words")
eq(PersonRefresh.replied(PersonRefresh.start(.idle, person: nil, now: t0), status: 429, nextAllowedAt: nil, now: t0).until, t0 + 120, "429 without a time: 2 min")
eq(PersonRefresh.replied(PersonRefresh.start(.idle, person: nil, now: t0), status: 404, nextAllowedAt: nil, now: t0), .idle, "older hub: quiet")
eq(PersonRefresh.replied(PersonRefresh.start(.idle, person: nil, now: t0), status: 0, nextAllowedAt: nil, now: t0).phase, .failed, "no answer: failed")
eq(PersonRefresh.replied(.idle, status: 429, nextAllowedAt: t0, now: t0), .idle, "a late reply after it ended changes nothing")
eq(PersonRefresh.personChanged(PersonRefresh.start(.idle, person: Person(id: "n"), now: t0), Person(id: "n", at: t0 + 5)), .idle, "never located: any fix ends it")
eq(PersonRefresh.nextAllowedAt(Data(#"{"error":"Updated recently","nextAllowedAt":1790000120}"#.utf8)), 1_790_000_120, "nextAllowedAt parses")
eq(PersonRefresh.nextAllowedAt(Data("nope".utf8)), nil, "garbage body")
eq((try? JSONDecoder().decode(LocationSettings.self, from: Data(settingsJSON.utf8)))?.requiredBy, "Chris", "settings decode")

eq(ReportSchedule.intervalSeconds(everySeconds: 300), 300, "background interval")
eq(ReportSchedule.intervalSeconds(everySeconds: 5), 60, "at most once a minute")
eq(ReportSchedule.intervalSeconds(everySeconds: 99_999), 3600, "at least once an hour")
let lastFix = ReportSchedule.Last(at: t0, lat: 43.65, lon: -79.38)
check(ReportSchedule.shouldSend(last: nil, now: t0, lat: 43.65, lon: -79.38, everySeconds: 300), "first fix goes")
check(!ReportSchedule.shouldSend(last: lastFix, now: t0 + 120, lat: 43.65, lon: -79.38, everySeconds: 300), "standing still")
check(ReportSchedule.shouldSend(last: lastFix, now: t0 + 300, lat: 43.65, lon: -79.38, everySeconds: 300), "interval up")
check(!ReportSchedule.shouldSend(last: lastFix, now: t0 + 30, lat: 43.651, lon: -79.38, everySeconds: 300), "moved, too soon")
check(ReportSchedule.shouldSend(last: lastFix, now: t0 + 61, lat: 43.651, lon: -79.38, everySeconds: 300), "moved 110 m")
// A locate (someone tapped this person) is answered once: the app's silent push and the extension's
// location push for the same tap give ONE report with source "refresh".
check(LocatePush.shouldAnswer(lastAnsweredAt: nil, now: t0), "first locate is answered")
check(!LocatePush.shouldAnswer(lastAnsweredAt: t0, now: t0 + 5), "the same tap twice: one report")
check(LocatePush.shouldAnswer(lastAnsweredAt: t0, now: t0 + 121), "the next tap (2 min later) is answered")
check(LocatePush.shouldAnswer(lastAnsweredAt: t0 + 500, now: t0), "clock moved back")
var answers = 0
for t in [t0, t0 + 1, t0 + 3, t0 + 20] where LocatePush.shouldAnswer(lastAnsweredAt: answers == 0 ? nil : t0, now: t) { answers += 1 }
eq(answers, 1, "one report per locate")
eq(LocationPushRule.reportBody(lat: 1, lon: 2, accuracyM: 5, at: t0, speedMps: nil)["source"], .string("refresh"), "locate report source")
check(LocatePush.isLocate(["newman": ["type": "refresh"]]), "refresh push")
let metres = ReportSchedule.distanceMeters(43.65, -79.38, 43.651, -79.38)
check(metres > 105 && metres < 117, "distance \(metres)")
let placesJSON = #"[{"id":"home","name":"Home","lat":null,"lon":null,"radiusM":150,"builtIn":true},{"id":"pl_1","name":"School","lat":43.66,"lon":-79.39,"radiusM":250,"builtIn":false}]"#
let placeList = (try? JSONDecoder().decode([Place].self, from: Data(placesJSON.utf8))) ?? []
eq(placeList.count, 2, "places decode with an unplaced Home")
eq(placeList.first?.lat, nil, "home not placed yet")
check(LocatePush.isLocate(["aps": ["content-available": 1], "newman": ["type": "locate"]]), "locate push")
check(!LocatePush.isLocate(["aps": ["alert": "Door open"]]), "ordinary push")
let reportBody = LocationReportBody.make(lat: 43.6, lon: -79.4, accuracyM: nil, at: t0, speedMps: 1.5, source: "background")
eq(reportBody["accuracyM"], .null, "unknown accuracy is null")
eq(reportBody["source"], .string("background"), "report source")
eq((try? JSONDecoder().decode(ReportReply.self, from: Data(#"{"live":false,"everySeconds":300}"#.utf8)))?.everySeconds, 300, "report reply (live is ignored)")

// MARK: today (GET /api/today, server/newman/today.py)

let todayJSON = #"""
{"date":"2026-10-02",
 "todo":[
  {"id":"upkeep.furnace","source":"upkeep","entity":"upkeep.furnace","title":"Furnace filter","owner":"Chris","ownerId":"p1","askWho":false,"level":"overdue","status":"4 days late","sort":-4,"action":"done"},
  {"id":"ring.side_door:health","source":"device","entity":"ring.side_door","level":"device","title":"Side door: battery low","status":"battery low","action":"open","askWho":false},
  {"id":"upkeep.plants","source":"upkeep","entity":"upkeep.plants","title":"Water the plants","owner":"","level":"due","status":"Due today","action":"done"},
  {"id":"lg.dishwasher:need:rinse aid low","source":"appliance","entity":"lg.dishwasher","level":"appliance","title":"Dishwasher: rinse aid low","status":"Reported by the dishwasher","action":"dismiss"}],
 "soon":[{"id":"upkeep.smoke","source":"upkeep","entity":"upkeep.smoke","title":"Test smoke alarms","level":"soon","status":"In 3 days","action":"done"}],
 "events":[{"title":"Dentist","allDay":false,"time":"3:30 PM","start":"2026-10-02T15:30:00","end":"2026-10-02T16:30:00","location":"Main St","calendar":"Family","colour":"#7FB8FF","now":false}],
 "headsUp":[{"id":"weather.home:rain","source":"weather","icon":"rain","entity":"weather.home","text":"Rain likely from 3 PM (70%)","short":"rain from 3 PM"}],
 "counts":{"todo":4,"soon":1,"events":1}}
"""#
if let brief = try? JSONDecoder().decode(TodayBriefing.self, from: Data(todayJSON.utf8)) {
    let card = TodayView.card(brief, done: [:], now: t0)
    eq(card.summary, "4 things to do · 1 event", "today summary")
    eq(card.rows.map { $0.id }, ["upkeep.furnace", "ring.side_door:health", "upkeep.plants", "lg.dishwasher:need:rinse aid low"], "today rows")
    eq(card.more, 1, "coming up waits behind N more")
    eq(card.rows[0].status, "4 days late · Chris", "owner named")
    eq(card.rows[0].tone, .urgent, "overdue is urgent")
    eq(card.rows[0].buttons, [.done, .snooze], "upkeep: done + tomorrow")
    eq(card.rows[1].buttons, [.open], "device: open")
    eq(card.rows[2].status, "Due today", "shared item names nobody")
    eq(card.rows[3].buttons, [.dismiss], "appliance: dismiss")
    eq(card.events.first?.title, "Dentist", "event")
    eq(card.headsUp.first?.text, "Rain likely from 3 PM (70%)", "heads-up")
    eq(TodayView.upkeepId(brief.todo[0]), "furnace", "upkeep id")
    let furnace = brief.todo[0]
    let after = TodayBriefing(date: brief.date, todo: Array(brief.todo.dropFirst()), soon: brief.soon, events: brief.events, headsUp: brief.headsUp)
    let doneHere = [furnace.id: TodayView.Done(item: furnace, upkeepId: "furnace", at: t0 - 30)]
    let card2 = TodayView.card(after, done: doneHere, now: t0)
    eq(card2.summary, "3 things to do · 1 event", "after done")
    eq(card2.rows.first { $0.id == furnace.id }?.buttons, [.undo], "undo offered")
    eq(card2.rows.first { $0.id == furnace.id }?.tone, .done, "struck through")
    let card3 = TodayView.card(after, done: doneHere, now: t0 + 700)
    eq(card3.rows.map { $0.id }, ["ring.side_door:health", "upkeep.plants", "lg.dishwasher:need:rinse aid low", "upkeep.smoke"], "done drops off, coming up fills in")
    eq(TodayView.card(TodayBriefing(), done: [furnace.id: TodayView.Done(item: furnace, upkeepId: "furnace", at: t0)], now: t0).summary,
       "All done for today", "all done")
    check(TodayView.card(TodayBriefing(), done: [:], now: t0).empty, "empty briefing")
} else { check(false, "today decodes") }
eq(TodayView.greeting(hour: 7), "Good morning", "morning")
eq(TodayView.greeting(hour: 18), "Good evening", "evening")

// MARK: phone tabs (three pages, then People and Alerts)

let tabPages = dashboard.pages ?? []
let plan = PhoneTabs.plan(tabPages)
eq(plan.tabs.map { $0.id }, ["overview", "security", "lights"], "three page tabs")
eq(plan.more.map { $0.id }, ["climate"], "climate has no tab")
check(plan.today == nil, "no today page in the fixture")
let overviewDial = (tabPages.first { $0.id == "overview" }?.tiles ?? []).first { $0.type == "dial" }
eq(PhoneTabs.pageForDial(plan, entityId: overviewDial?.string("entity"))?.id, "climate", "thermostat tile opens Climate")
check(PhoneTabs.pageForDial(plan, entityId: "nothing.here") == nil, "unknown dial")
let todayPage = DashboardPage(id: "today", name: "Today", icon: "sun", tiles: [
    Tile(id: "g", type: "greeting", x: 0, y: 0, w: 12, h: 1), Tile(id: "t", type: "todo", x: 0, y: 1, w: 5, h: 7),
    Tile(id: "c", type: "calendar", x: 5, y: 1, w: 4, h: 7), Tile(id: "w", type: "weather", x: 9, y: 1, w: 3, h: 7),
])
let planToday = PhoneTabs.plan([todayPage] + tabPages)
eq(planToday.today?.id, "today", "today page found")
eq(planToday.tabs.map { $0.id }, ["overview", "security", "lights"], "today isn't a tab")
check(!PhoneTabs.isTodayPage(DashboardPage(id: "x", name: "Home", icon: nil, tiles: [Tile(id: "t", type: "todo", x: 0, y: 0, w: 4, h: 4),
                                                                                     Tile(id: "d", type: "devices", x: 4, y: 0, w: 4, h: 4)])), "mixed page is ordinary")
let todayCells = PhoneLayout.cells(for: todayPage)
eq(todayCells.map { $0.id }, ["t", "c", "w"], "greeting not drawn on the phone")
eq(todayCells.first?.span, 4, "to do is full width")
check(PhoneTabs.hasTodayTile(todayPage), "today tile detected")
check(PhoneTabs.showsTodayCard(plan, page: tabPages[0]) && !PhoneTabs.todayWhenEmpty(plan), "flowed: today card unless the page has one")
check(!plan.explicit, "dashboard plan isn't explicit")

// MARK: phone layouts (GET /api/phone-layout)

let layoutJSON = #"""
{"source": "own", "futureField": {"x": 1},
 "layout": {"today": true, "density": "cosy",
   "pages": [
     {"id": "home", "name": "Home", "icon": "home", "tab": true, "colour": "teal",
      "tiles": [
        {"id": "t1", "type": "devices", "x": 0, "y": 0, "w": 4, "h": 2, "config": {"entities": ["a", "b"]}, "textSize": 120, "pinned": true},
        {"id": "t2", "type": "dial", "x": 2, "y": 2, "w": 2, "h": 2, "config": {"entity": "th"}}
      ]},
     {"id": "climate", "name": "Climate", "icon": "thermo", "tab": false,
      "tiles": [{"id": "c1", "type": "dial", "x": 0, "y": 0, "w": 4, "h": 4, "config": {"entity": "th"}}]},
     {"id": "sec", "name": "Security", "icon": "shield", "tab": true, "tiles": []}
   ]}}
"""#
if let reply = try? PhoneGrid.parse(Data(layoutJSON.utf8)) {
    eq(reply.source, "own", "layout source")
    check(reply.layout.today, "layout today")
    eq(reply.layout.pages.map { $0.id }, ["home", "climate", "sec"], "layout pages (unknown fields ignored)")
    eq(reply.layout.pages.map { $0.tab ?? false }, [true, false, true], "layout tab flags")
    let t1 = reply.layout.pages[0].tiles?[0]
    eq([t1?.x, t1?.y, t1?.w, t1?.h], [0, 0, 4, 2], "tile place")
    eq(t1?.textSize, 120, "tile textSize")
    eq(t1?.textScale ?? 0, 1.2, "tile text scale")
    let t2 = reply.layout.pages[0].tiles?[1]
    check(t2?.textSize == nil && t2?.textScale == 1, "missing textSize = normal")
    eq(t2?.string("entity"), "th", "tile config")

    let lp = PhoneGrid.plan(reply.layout)
    eq(lp.tabs.map { $0.id }, ["home", "sec"], "layout tabs")
    eq(lp.more.map { $0.id }, ["climate"], "non-tab pages are links")
    check(lp.explicit && lp.todayCard && lp.today == nil, "layout plan flags")
    check(PhoneTabs.showsTodayCard(lp, page: lp.tabs[0]) && PhoneTabs.todayWhenEmpty(lp), "layout asks for the today card")
    eq(PhoneTabs.pageForDial(lp, entityId: "th")?.id, "climate", "thermostat opens its page")
    eq(PhoneGrid.next(nil, PhoneGrid.outcome(status: 200, body: Data(layoutJSON.utf8)))?.pages.count, 3, "200 = the layout")
} else { check(false, "phone layout decodes") }

if let bare = try? PhoneGrid.parse(Data(#"{"layout": {"pages": [{"id": "p", "name": "P", "tiles": [{"id": "t", "type": "weather"}]}]}}"#.utf8)) {
    eq(bare.source, "default", "default source")
    check(!bare.layout.today && bare.layout.pages[0].tab == nil, "missing flags")
    eq(bare.layout.pages[0].tiles?[0].w, 1, "missing size")
} else { check(false, "bare phone layout decodes") }
eq(Tile(id: "v", type: "values", x: 0, y: 0, w: 1, h: 1, config: ["textSize": .number(80)]).textScale, 0.8, "config textSize still works")

func gp(_ id: String, _ tab: Bool) -> DashboardPage { DashboardPage(id: id, name: id, icon: nil, tiles: [], tab: tab) }
let six = PhoneGrid.plan(PhoneGridLayout(today: false, pages: [gp("a", true), gp("b", false), gp("c", true), gp("d", true), gp("e", true), gp("f", false)]))
eq(six.tabs.map { $0.id }, ["a", "c", "d"], "at most three tabs, in order")
eq(six.more.map { $0.id }, ["b", "e", "f"], "the rest are links")
check(!PhoneTabs.showsTodayCard(six, page: six.tabs[0]), "no today card unless asked")
eq(PhoneGrid.plan(PhoneGridLayout(today: false, pages: [gp("a", false), gp("b", false)])).tabs.map { $0.id }, ["a"], "no tabs: the first page")

func gt(_ id: String, _ x: Int, _ y: Int, _ w: Int, _ h: Int, _ type: String = "camera") -> Tile { Tile(id: id, type: type, x: x, y: y, w: w, h: h) }
eq(PhoneGrid.cellSize(width: 358, gap: 10), 82, "cell size")
eq(PhoneGrid.rect(gt("a", 0, 0, 1, 1), width: 358, gap: 10), PhoneGrid.Rect(x: 0, y: 0, width: 82, height: 82), "1x1 rect")
eq(PhoneGrid.rect(gt("b", 2, 1, 2, 3), width: 358, gap: 10), PhoneGrid.Rect(x: 184, y: 92, width: 174, height: 266), "2x3 rect")
eq(PhoneGrid.rect(gt("c", 0, 0, 4, 1), width: 358, gap: 10).width, 358, "full width")
eq(PhoneGrid.height([gt("a", 0, 0, 1, 1), gt("b", 2, 1, 2, 3)], width: 358, gap: 10), 358, "grid height to the lowest tile")
eq(PhoneGrid.height([], width: 358, gap: 10), 0, "empty grid")
let cl = PhoneGrid.clamp(gt("x", 3, -2, 3, 12))
eq([cl.x, cl.y, cl.w, cl.h], [3, 0, 1, 8], "clamped into the grid")
eq(PhoneGrid.placed([gt("a", 0, 3, 1, 1), gt("b", 2, 0, 1, 1), gt("c", 0, 1, 1, 1)]).map { $0.id }, ["b", "c", "a"], "reading order")
check(PhoneGrid.isCompact(gt("w", 0, 0, 2, 2, "weather")) && !PhoneGrid.isCompact(gt("w", 0, 0, 4, 2, "weather")), "compact weather")
check(PhoneGrid.isCompact(gt("d", 0, 0, 4, 3, "dial")) && !PhoneGrid.isCompact(gt("d", 0, 0, 4, 4, "dial")), "compact dial")

eq(PhoneGrid.fitDevices(count: 5, width: 358, height: 266, titled: false), PhoneGrid.DeviceFit(columns: 2, rows: 3, shown: 5, more: 0), "devices fit")
eq(PhoneGrid.fitDevices(count: 9, width: 358, height: 266, titled: false), PhoneGrid.DeviceFit(columns: 2, rows: 3, shown: 5, more: 4), "+N more")
eq(PhoneGrid.fitDevices(count: 4, width: 174, height: 174, titled: true), PhoneGrid.DeviceFit(columns: 1, rows: 2, shown: 1, more: 3), "narrow list")
eq(PhoneGrid.fitDevices(count: 2, width: 358, height: 174, titled: false, list: true).columns, 1, "list look stays a list")
eq(PhoneGrid.fitDevices(count: 3, width: 82, height: 40, titled: true), PhoneGrid.DeviceFit(columns: 1, rows: 1, shown: 0, more: 3), "at least a row")

let keep = PhoneGridLayout(today: true, pages: [])
eq(PhoneGrid.outcome(status: 404, body: Data(#"{"error": "Not found"}"#.utf8)), .unsupported, "404 = older hub")
check(PhoneGrid.next(keep, .unsupported) == nil, "404 falls back to the flowed dashboard")
eq(PhoneGrid.outcome(status: 500, body: nil), .failed, "5xx")
eq(PhoneGrid.outcome(status: 200, body: Data("<html>".utf8)), .failed, "garbled reply")
eq(PhoneGrid.next(keep, .failed), keep, "a failure keeps the layout")
check(PhoneGrid.next(nil, .failed) == nil, "a failure without a layout stays flowed")

// MARK: location pushes (LocationPushExtension)

check(LocationPushRule.shouldRespond(sharing: true, signedIn: true, locationDenied: false), "extension answers while sharing")
check(!LocationPushRule.shouldRespond(sharing: false, signedIn: true, locationDenied: false), "not sharing: nothing")
check(!LocationPushRule.shouldRespond(sharing: true, signedIn: false, locationDenied: false), "signed out: nothing")
check(!LocationPushRule.shouldRespond(sharing: true, signedIn: true, locationDenied: true), "location denied: nothing")
check(LocationPushRule.shouldMonitor(sharing: true, signedIn: true, always: true), "monitor with Always")
check(!LocationPushRule.shouldMonitor(sharing: true, signedIn: true, always: false), "no Always: no location pushes")
check(!LocationPushRule.shouldMonitor(sharing: false, signedIn: true, always: true), "sharing off: no location pushes")
check(LocationPushRule.usableCachedFix(age: 30, accuracy: 15), "a 30 s old fix will do")
check(!LocationPushRule.usableCachedFix(age: 300, accuracy: 15), "a 5 min old fix won't")
check(!LocationPushRule.usableCachedFix(age: 5, accuracy: -1), "an invalid fix won't")
eq(LocationPushRule.postTimeout(elapsed: 2), 12.5, "report gets the rest of the budget")
eq(LocationPushRule.postTimeout(elapsed: 14.9), 2, "but never less than 2 s")
check(LocationPushRule.budgetSeconds <= 15 && LocationPushRule.fixWaitSeconds < LocationPushRule.budgetSeconds, "time budget")
let lpReport = LocationPushRule.reportBody(lat: 43.65, lon: -79.38, accuracyM: nil, at: 1_790_000_000, speedMps: 1.5)
eq(lpReport["source"], .string("refresh"), "extension reports are one-shot refreshes")
eq(lpReport["accuracyM"], .null, "unknown accuracy is null")
eq(lpReport["speedMps"], .number(1.5), "speed kept")
eq(LocationPushRule.tokenBody(deviceId: "ios-1", token: "abcd", sandbox: true),
   ["deviceId": .string("ios-1"), "token": .string("abcd"), "env": .string("sandbox")], "push-token body")
eq(LocationPushRule.tokenBody(deviceId: "ios-1", token: "abcd", sandbox: false)["env"], .string("production"), "Ad Hoc: production")
eq(LocationPushRule.forgetBody(deviceId: "ios-1"), ["deviceId": .string("ios-1"), "token": .null], "forget body")
let lpJSON = String(decoding: JSONBody.encode(LocationPushRule.forgetBody(deviceId: "ios-1")), as: UTF8.self)
check(lpJSON.contains("\"token\":null"), "forget body sends token null: \(lpJSON)")
eq(LocationPushRule.appGroup, "group.ca.nimaste.newman", "App Group")
eq(PushPayload.hexToken(Data([0x0a, 0xff, 0x10])), "0aff10", "location push token as hex")

// MARK: people and greeting tiles (web/src/wall/PhoneTiles.jsx)

func tilePerson(_ id: String, _ name: String, sharing: Bool = true, home: Bool = false) -> Person {
    Person(id: id, name: name, sharing: sharing, home: home, lat: sharing ? 43.6 : nil, lon: sharing ? -79.4 : nil, at: t0 - 240)
}
let tileHousehold = [tilePerson("a", "Chris", home: true), tilePerson("b", "Marlina"), tilePerson("c", "Massimo", sharing: false),
                     tilePerson("d", "Nonna"), tilePerson("e", "Zia")]
eq(PeopleTileLogic.select(tileHousehold, pick: []).map { $0.id }, ["a", "b", "c", "d", "e"], "people tile: everyone")
eq(PeopleTileLogic.select(tileHousehold, pick: ["d", "b", "gone"]).map { $0.id }, ["b", "d"], "people tile: chosen, in the hub's order")
eq(TileFit.list(count: 5, avail: 100, row: 44, gap: 6, moreH: 0), TileFit.Fit(shown: 2, more: 3, left: 6), "fit rows")
eq(TileFit.list(count: 2, avail: 100, row: 44, gap: 6, moreH: 18), TileFit.Fit(shown: 2, more: 0, left: 6), "all fit")
eq(TileFit.list(count: 3, avail: 100, row: 44, gap: 6, moreH: 18), TileFit.Fit(shown: 1, more: 2, left: 56), "room kept for +N more")
eq(TileFit.list(count: 3, avail: 30, row: 44, gap: 6, moreH: 18), TileFit.Fit(shown: 0, more: 3, left: 30), "no room for a row")
eq(TileFit.list(count: 4, avail: .infinity, row: 44, gap: 6, moreH: 0).shown, 4, "flowed: all rows")
eq(TileFit.rowsIn(-20, row: 44, gap: 6), 0, "negative room")
typealias TileLayout = PeopleTileLogic.Layout
eq(PeopleTileLogic.layout(count: 5, width: 330, height: 238),
   TileLayout(look: .rows, head: true, shown: 4, more: 1, moreInHead: true, moreBelow: false, avatars: true), "4x3: +1 more by the title")
eq(PeopleTileLogic.layout(count: 3, width: 330, height: 238),
   TileLayout(look: .rows, head: true, shown: 3, more: 0, moreInHead: false, moreBelow: false, avatars: true), "4x3: everyone fits")
eq(PeopleTileLogic.layout(count: 5, width: 146, height: 146),
   TileLayout(look: .rows, head: true, shown: 2, more: 3, moreInHead: true, moreBelow: false, avatars: true), "2x2")
eq(PeopleTileLogic.layout(count: 5, width: 54, height: 146),
   TileLayout(look: .rows, head: true, shown: 2, more: 3, moreInHead: false, moreBelow: true, avatars: false), "1x2: +N more under the rows")
eq(PeopleTileLogic.layout(count: 5, width: 330, height: 54).look, .oneLine, "4x1: one line")
eq(PeopleTileLogic.layout(count: 1, width: 330, height: 54),
   TileLayout(look: .rows, head: false, shown: 1, more: 0, moreInHead: false, moreBelow: false, avatars: true), "one person in one row")
eq(PeopleTileLogic.layout(count: 5, width: 330, height: .infinity).shown, 5, "flowed: everyone")
eq(PeopleTileLogic.oneLine(Array(tileHousehold.prefix(3))), "Chris (home) · Marlina · Massimo", "one-line people")
eq(PeopleText.mapLine(tileHousehold[0], now: t0), "Home · updated 4 min ago", "tile row: where and when")
let notSharingRow = PeopleText.row(tileHousehold[2], now: t0)
check(!notSharingRow.located && notSharingRow.whereText == "Not sharing location" && notSharingRow.updated == nil, "not sharing: dim, not tappable")

let greetingJSON = #"""
{"todo":[{"id":"upkeep.furnace","source":"upkeep","title":"Furnace filter","level":"overdue","action":"done"},
         {"id":"upkeep.plants","source":"upkeep","title":"Water the plants","level":"due","action":"done"},
         {"id":"lg.dishwasher:need","source":"appliance","title":"Dishwasher: rinse aid low","level":"appliance","action":"dismiss"}],
 "events":[{"title":"Dentist"},{"title":"Soccer"}],
 "headsUp":[
  {"id":"w:1","source":"weather","icon":"warning","text":"Special weather statement","short":"special weather statement","level":"statement"},
  {"id":"w:2","source":"weather","icon":"warning","text":"Wind warning","level":"warning"},
  {"id":"cal","source":"calendar","icon":"calendar","text":"Dentist tomorrow"},
  {"id":"w:rain","source":"weather","icon":"rain","text":"Rain likely from 3 PM (70%)","short":"rain from 3 PM"}]}
"""#
if let gb = try? JSONDecoder().decode(TodayBriefing.self, from: Data(greetingJSON.utf8)) {
    eq(TodayView.greetingLine(gb, done: [:], now: t0), "3 things to do · 2 events today · wind warning · rain from 3 PM", "greeting line")
    let filterItem = gb.todo[0]
    let doneNow = [filterItem.id: TodayView.Done(item: filterItem, upkeepId: "furnace", at: t0 - 30)]
    eq(TodayView.greetingLine(gb, done: doneNow, now: t0), "2 things to do · 2 events today · wind warning · rain from 3 PM", "done here")
    check(TodayView.greetingLine(gb, done: doneNow, now: t0 + 700).hasPrefix("3 things to do · "), "done here drops off")
    eq(TodayView.greetingLine(TodayBriefing(todo: [filterItem], events: [gb.events[0]]), done: [:], now: t0), "1 thing to do · 1 event today", "singular")
} else { check(false, "greeting briefing decodes") }
eq(TodayView.greetingLine(TodayBriefing(), done: [:], now: t0), "Nothing to do", "nothing to do")
eq(TodayView.greeting(hour: 0), "Good morning", "midnight")
eq(TodayView.greeting(hour: 11), "Good morning", "11 am")
eq(TodayView.greeting(hour: 12), "Good afternoon", "noon")
eq(TodayView.greeting(hour: 17), "Good afternoon", "5 pm")
eq(TodayView.greetingFit(height: 54), TodayView.GreetingFit(size: 23, twoLines: true), "one row of cells: two lines")
eq(TodayView.greetingFit(height: 30), TodayView.GreetingFit(size: 14, twoLines: false), "short: one line")
eq(TodayView.greetingFit(height: 200), TodayView.GreetingFit(size: 26, twoLines: true), "tall: capped size")
eq(TodayView.greetingFit(height: .infinity), TodayView.GreetingFit(size: 26, twoLines: true), "flowed")

runVoiceTests()
runWallScreenTests()
runLiveStatusTests()
runMoreTests()
runControlsTests()
runMuteTests()
runHomeTests()
runScreensTests()
runCategoryTests()
print("\(passes) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
