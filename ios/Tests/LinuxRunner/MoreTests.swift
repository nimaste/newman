import Foundation

// The More tab's lists (Shared/Core/MoreLogic.swift): automations and the device directory.
// Called from main.swift.

func runMoreTests() {
    var utc = Calendar(identifier: .gregorian)
    utc.timeZone = TimeZone(identifier: "UTC")!
    let now = utc.date(from: DateComponents(year: 2026, month: 10, day: 16, hour: 15, minute: 0))!
    let t = now.timeIntervalSince1970

    // MARK: automations
    let json = #"""
    [{"id":"a1","name":"Doorbell alert","enabled":true,"summary":"When someone rings...","lastRun":{"id":"r1","startedAt":\#(t - 4 * 86400),"status":"done"}},
     {"id":"a2","name":"Lock Notification","enabled":true,"lastRun":{"id":"r2","startedAt":\#(t - 360),"status":"done"}},
     {"id":"a3","name":"Basement too cold","enabled":false,"lastRun":null},
     {"id":"a4","name":"Garage door left open","lastRun":{"startedAt":\#(t - 18 * 3600),"status":"skipped"}},
     {"id":"a5","name":"Nightly lock check","enabled":true,"lastRun":{"id":"r5","startedAt":\#(t - 3600),"status":"sent"}}]
    """#
    guard let list = try? JSONDecoder().decode([AutomationSummary].self, from: Data(json.utf8)) else {
        check(false, "automations decode")
        return
    }
    eq(list.count, 5, "automations decoded")
    eq(list[3].enabled, true, "enabled defaults to on")
    eq(list[3].lastRun?.id ?? "", "\(t - 18 * 3600)", "a run without id gets one")
    eq(AutomationList.sorted(list).map { $0.id }, ["a2", "a5", "a4", "a1", "a3"], "latest run first, never run last")
    eq(AutomationList.countLine(list), "5 automations · 1 off", "count line with one off")
    eq(AutomationList.countLine(AutomationList.setting(list, id: "a3", enabled: true)), "5 automations · all on", "all on")
    eq(AutomationList.countLine([]), "No automations yet", "none")
    eq(AutomationList.countLine([list[0]]), "1 automation · on", "one")
    eq(AutomationList.ranToday(list, now: now, calendar: utc), 2, "ran today")
    eq(AutomationList.headerLine(list, now: now, calendar: utc), "5 automations · 1 off · 2 ran today", "header line")
    check(AutomationList.lastRunLine(list[1].lastRun, now: now, calendar: utc).hasPrefix("Ran today "), "ran today line")
    check(AutomationList.lastRunLine(list[4].lastRun, now: now, calendar: utc).hasPrefix("Sent today "), "sent line")
    let skipped = AutomationList.lastRunLine(list[3].lastRun, now: now, calendar: utc)
    check(skipped.hasPrefix("Checked yesterday ") && skipped.hasSuffix(" · nothing to do"), "skipped line: \(skipped)")
    let older = AutomationList.lastRunLine(list[0].lastRun, now: now, calendar: utc)
    // "Ran Oct 12, 3:00 PM" (the month and day in the phone's own order: "12 Oct" elsewhere).
    check(older.hasPrefix("Ran ") && older.contains("12") && older.contains("Oct") && !older.contains("today"), "older run line: \(older)")
    eq(AutomationList.lastRunLine(nil, now: now, calendar: utc), "Hasn't run yet", "never run")
    eq(AutomationList.statusLabel("skipped"), "Nothing to do", "skipped status")
    eq(AutomationList.statusLabel("done"), "Done", "done status")
    eq(AutomationList.enabledBody(false), ["enabled": JSONValue.bool(false)], "patch body")
    eq(AutomationList.setting(list, id: "a1", enabled: false).first { $0.id == "a1" }?.enabled ?? true, false, "switched off locally")

    // MARK: devices, from the captured hub
    let meta = load("meta", as: Meta.self)
    let kinds = meta.kindsById
    let entities = load("entities", as: [Entity].self)
    let sections = DeviceDirectory.sections(entities, kinds: kinds, categories: meta.categories ?? [])
    eq(sections.flatMap { $0.ids }.count, entities.count, "every device is in one section")
    let order = (meta.categories ?? []).map { $0.id }
    let known = sections.map { $0.id }.filter { order.contains($0) }
    eq(known, order.filter { known.contains($0) }, "sections in the hub's category order")
    check(sections.allSatisfy { !$0.title.isEmpty }, "sections have titles")
    for s in sections {
        let flags = s.ids.compactMap { id in entities.first { $0.id == id } }.map(DeviceDirectory.needsAttention)
        check(flags == flags.sorted { $0 && !$1 }, "\(s.id): needing attention first")
    }
    eq(DeviceDirectory.matching(entities, kinds: kinds, query: "", filter: .all).count, entities.count, "all")
    let bad = DeviceDirectory.matching(entities, kinds: kinds, query: "", filter: .attention)
    check(bad.allSatisfy(DeviceDirectory.needsAttention), "attention filter")
    let on = DeviceDirectory.matching(entities, kinds: kinds, query: "", filter: .on)
    check(on.allSatisfy { DeviceLogic.isLit($0, kinds[$0.kind]) && $0.isAvailable }, "on filter")
    if let first = entities.first {
        let q = String(first.name.prefix(4)).uppercased()
        check(DeviceDirectory.matching(entities, kinds: kinds, query: q, filter: .all).contains { $0.id == first.id },
              "search ignores case")
    }
    eq(DeviceDirectory.matching(entities, kinds: kinds, query: "zzqq-none", filter: .all).count, 0, "search with no match")

    var sick = Entity(id: "x.keypad", name: "Hallway Keypad", kind: entities.first?.kind ?? "", state: "clear")
    sick.health = Health(ok: false, issues: ["Battery failed", "No AC power", "Not responding to the hub"])
    check(DeviceDirectory.needsAttention(sick), "unhealthy needs attention")
    eq(DeviceDirectory.statusLine(sick, kinds[sick.kind]), "Battery failed · No AC power · Not responding to the hub", "issues joined")
    var gone = Entity(id: "x.gone", name: "Gone", kind: "", state: "offline")
    gone.available = false
    check(DeviceDirectory.needsAttention(gone), "offline needs attention")
    eq(DeviceDirectory.statusLine(gone, nil), "Offline", "offline line")
    eq(DeviceDirectory.countLine([sick, gone]), "2 devices · 2 need attention", "count line plural")
    eq(DeviceDirectory.countLine([sick]), "1 device · 1 needs attention", "count line singular")
    eq(DeviceDirectory.category(Entity(id: "x", name: "x", kind: "nope"), nil), "other", "no category is other")

    // A light's on/off pair is a switch; a lock (sensitive unlock) is not.
    let switchable = kinds.values.filter { DeviceDirectory.switchPair($0) != nil }
    check(!switchable.isEmpty, "some kinds switch on and off")
    check(switchable.allSatisfy { k in !DeviceLogic.plainCommands(k).contains { $0.isSensitive } }, "no sensitive switch")
}
