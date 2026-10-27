import Foundation

// The phone screens' shared wording (Shared/Core/PhoneScreens.swift). Called from main.swift.

func runScreensTests() {
    // Home cards: one table of icons and colours by screen role, and the value's size by its length.
    eq(HomeCards.style(role: "climate")?.color, HomeCards.orange, "climate: orange")
    eq(HomeCards.style(role: "security")?.symbol, "shield.lefthalf.filled", "security: shield")
    eq(HomeCards.style(role: "people")?.color, HomeCards.purple, "people: purple")
    check(HomeCards.style(role: "cleaning") == nil, "a category without a screen: no fixed style")
    check(HomeCards.style(role: nil) == nil, "no role: no fixed style")
    eq(HomeCards.valueSize("21.8°"), 30, "a number: 30")
    eq(HomeCards.valueSize("All locked"), 22, "a word or two: 22")
    eq(HomeCards.valueSize("Garage door open"), 18, "longer: 18")

    // Status: ok, warn (no green line), danger.
    func status(_ json: String) -> HomeData.Status? {
        (try? JSONDecoder().decode(HomeData.self, from: Data(#"{"status":"#.utf8) + Data(json.utf8) + Data("}".utf8)))?.status
    }
    eq(status(#"{"level":"ok","summary":"Locked up"}"#)?.mode, .ok, "ok")
    eq(status(#"{"level":"warn","summary":"x","warnings":[{"entity":"a","issue":"b","text":"A · B"}]}"#)?.mode, .warn, "warn")
    eq(status(#"{"level":"danger","danger":[{"id":"g","title":"Garage door open","detail":""}]}"#)?.mode, .danger, "danger")

    // More's lines.
    eq(MoreLines.people([tilePerson("a", "Chris", home: true), tilePerson("b", "Sam")]), "Chris is home", "one home")
    eq(MoreLines.people([tilePerson("a", "Chris", home: true), tilePerson("b", "Sam", home: true)]), "2 people home", "two home")
    eq(MoreLines.people([tilePerson("a", "Chris")]), "No one is home", "no one")
    eq(MoreLines.automations([AutomationSummary(id: "1", name: "a"), AutomationSummary(id: "2", name: "b")]), "2 · all on", "all on")
    eq(MoreLines.automations([AutomationSummary(id: "1", name: "a", enabled: false), AutomationSummary(id: "2", name: "b")]),
       "2 · 1 off", "one off")

    // One camera's detections (GET /api/alerts?entity=).
    let detJSON = #"""
    [{"id":"r:1","at":1792400100,"label":"Person","zone":"Driveway","snapshot":"api/cameras/frigate/reviews/1/snapshot.jpg","clip":"api/clips/frigate/r:1","start":1792400100,"objects":["person"]},
     {"id":"r:2","at":1792399000,"label":"Car","zone":null,"snapshot":null,"clip":null},
     {"id":"r:3","at":1792398000,"label":"Person","zone":"Walkway","clip":"api/clips/frigate/r:3"}]
    """#
    let dets = (try? JSONDecoder().decode([Detection].self, from: Data(detJSON.utf8))) ?? []
    eq(dets.count, 3, "detections decode")
    eq(DetectionLogic.chips(dets), [DetectionLogic.Chip(label: "Person", count: 2), DetectionLogic.Chip(label: "Car", count: 1)],
       "a chip per label with counts")
    eq(AlertClip.clipId(fromPath: "api/clips/frigate/r:1"), "frigate:r:1", "clip path back to its id")
    eq(AlertClip.clipId(fromPath: "frigate:r:1"), "frigate:r:1", "an id stays")
    eq(AlertClip.clipId(fromPath: nil), nil, "no clip")
    eq(AlertClip.path("api/clips/frigate/r:1"), "api/clips/frigate/r:1", "a signed-in clip path plays as it is")
    eq(AlertClip.path("api/clips/"), nil, "an empty clip path")
    let fromList = try? JSONDecoder().decode(CameraAlert.self, from: Data(#"{"id":"r:1","clip":"api/clips/frigate/r:1"}"#.utf8))
    eq(AlertClip.match(fromList.map { [$0] } ?? [], clip: "frigate:r:1", reviewId: nil)?.id, "r:1",
       "a push's clip finds the alert the hub now sends as a path")
    eq(AlertClip.path(dets[0].cameraAlert(camera: "frigate.garage").clip), "api/clips/frigate/r%3A1", "plays through the signed-in API")
    eq(dets[0].cameraAlert(camera: "frigate.garage").entityId, "frigate.garage", "the camera")
    eq(DetectionLogic.line(dets[0]) { _ in "8:35 AM" }, "Driveway · 8:35 AM", "zone · time")
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "America/Toronto")!
    let now = Date(timeIntervalSince1970: 1792400100)
    let start = cal.startOfDay(for: now).timeIntervalSince1970
    eq(DetectionLogic.since(daysBack: 0, now: now, calendar: cal), start, "today: from midnight")
    eq(DetectionLogic.since(daysBack: 1, now: now, calendar: cal), start - 86400, "earlier days: a day more")

    // A tapped notification: the run's activity entry.
    let camEv = try? JSONDecoder().decode(ActivityEvent.self, from: Data(#"""
    {"id":41,"ts":1792400100,"kind":"automation","title":"Person at the garage","body":"","entityId":"frigate.garage",
     "data":{"runId":"run1","clip":"frigate:r:9","snapshotId":"s9"}}
    """#.utf8))
    if let camEv, case .camera(let cam, let alert) = NotificationAlert.target(camEv) {
        eq(cam, "frigate.garage", "a camera alert opens its camera")
        eq(alert.clip, "frigate:r:9", "and plays its clip")
        eq(alert.snapshotUrl, "api/snapshots/s9", "its snapshot while it loads")
    } else {
        check(false, "a camera run opens the camera")
    }
    let plainEv = try? JSONDecoder().decode(ActivityEvent.self, from: Data(#"{"id":42,"ts":1,"kind":"automation","title":"Leak","data":{"runId":"r2"}}"#.utf8))
    eq(plainEv.map(NotificationAlert.target), .alerts("42"), "anything else: More › Alerts at that entry")

    // Security today, newest first.
    let sec = (try? JSONDecoder().decode([SecurityEntry].self, from: Data(#"[{"id":1,"at":10,"title":"A"},{"id":2,"at":30,"title":"B"}]"#.utf8))) ?? []
    eq(SecurityEntry.sorted(sec).map { $0.title }, ["B", "A"], "security: newest first")

    // Today: "+ Add" picks a calendar that takes tasks and Newman may write.
    let tc = (try? JSONDecoder().decode(TaskCalendars.self, from: Data(#"""
    {"calendars":[{"id":"caldav.home","name":"Home","colour":"#3CCFB4","writable":true},
                  {"id":"caldav.school","name":"School","writable":false}],
     "people":[{"id":"p1","name":"Chris"},{"id":"p2","name":"Marlina"}]}
    """#.utf8))) ?? TaskCalendars()
    eq(tc.writable.map { $0.name }, ["Home"], "task calendars: only writable ones for + Add")
    eq(tc.people.map { $0.name }, ["Chris", "Marlina"], "task calendars: the household")
    let calJSON = #"""
    [{"id":"cal.family","name":"Family","kind":"calendar","attrs":{"upcoming":[
       {"title":"Dentist","start":"2026-10-22T09:00:00-04:00"},{"title":"Today thing","start":"2026-10-19T09:00:00-04:00"}]}},
     {"id":"light.a","name":"Lamp","kind":"light"}]
    """#
    let ents = (try? JSONDecoder().decode([Entity].self, from: Data(calJSON.utf8))) ?? []
    let cals = TodayScreenLogic.calendars(ents)
    eq(cals.map { $0.id }, ["cal.family"], "calendars: whatever carries upcoming events")
    eq(TodayScreenLogic.emptyCalendarLine(cals), "Nothing on the Family calendar today", "empty calendar line")
    let today = TodayScreenLogic.parse("2026-10-19T12:00:00-04:00")!
    let next = TodayScreenLogic.comingUp(cals, now: today, calendar: cal)
    eq(next.map { $0.title }, ["Dentist"], "coming up: after today only")
    eq(next.first?.day ?? "", "Thursday", "a weekday this week")
    eq(TodayScreenLogic.dayString(today, calendar: cal), "2026-10-19", "a new task's due day")
}
