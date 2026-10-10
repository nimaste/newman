import Foundation

// Live status (Shared/Core/LiveStatus.swift): the hub's content-state as ActivityKit decodes it,
// the "+N more" row, the token bodies and matching GET /api/live/current. Called from main.swift.

func runLiveStatusTests() {
    let stateJSON = #"""
    {"title":"Garage open","subtitle":"Since you left","since":1760000000,"active":true,
     "devices":[{"id":"cover.garage","name":"Garage door","icon":"garage","stateLabel":"Open","active":true,
                 "actions":[{"command":"close","label":"Close","sensitive":false},{"command":"open","label":"Open","sensitive":true},
                            {"command":"stop","label":"Stop"}]},
                {"id":"lock.front","name":"Front door","icon":"lock","stateLabel":"Unlocked","active":true,"actions":[]},
                {"id":"binary.back","name":"Back door","stateLabel":"Open","active":false}],
     "more":2}
    """#
    guard let s = try? JSONDecoder().decode(LiveStatusState.self, from: Data(stateJSON.utf8)) else {
        check(false, "live content-state decodes")
        return
    }
    eq(s.title, "Garage open", "title")
    eq(s.subtitle, "Since you left", "subtitle")
    eq(s.since, 1760000000, "since (seconds since 1970, not ActivityKit's 2001 dates)")
    check(s.active, "active")
    eq(s.devices.count, 3, "devices")
    eq(s.more, 2, "more")
    eq(s.devices[0].actions[1], LiveStatusAction(command: "open", label: "Open", sensitive: true), "sensitive action")
    eq(s.devices[0].actions[2].sensitive, false, "sensitive defaults to false")
    eq(s.devices[2].actions, [], "no actions")
    eq(s.devices[2].icon, nil, "no icon")

    // Encodes back to the same field names (ActivityKit stores it that way).
    if let data = try? JSONEncoder().encode(s), let back = try? JSONDecoder().decode(LiveStatusState.self, from: data) {
        eq(back, s, "round trip")
        let text = String(decoding: data, as: UTF8.self)
        for k in ["\"title\"", "\"subtitle\"", "\"since\"", "\"active\"", "\"devices\"", "\"more\"", "\"stateLabel\"", "\"sensitive\""] {
            check(text.contains(k), "encodes \(k)")
        }
    } else { check(false, "content-state encodes") }

    // Lenient: missing fields don't drop the push.
    let bare = try? JSONDecoder().decode(LiveStatusState.self, from: Data(#"{"devices":[{"id":"a"}],"subtitle":null}"#.utf8))
    eq(bare?.title, "", "missing title")
    eq(bare?.subtitle ?? "nil", "nil", "null subtitle")
    eq(bare?.since, 0, "missing since")
    eq(bare?.active, true, "missing active")
    eq(bare?.devices.first?.name, "a", "name defaults to the id")
    eq(bare?.more, 0, "missing more")
    eq(try? JSONDecoder().decode(LiveStatusState.self, from: Data(#"{"more":-3}"#.utf8)).more, 0, "negative more")

    // Rows and "+N more".
    eq(LiveStatusLogic.shown(s).map(\.id), ["cover.garage", "lock.front"], "two rows")
    eq(LiveStatusLogic.moreCount(s, shown: 2), 3, "one listed without room + 2 more")
    eq(LiveStatusLogic.moreText(3), "+3 more", "more text")
    eq(LiveStatusLogic.moreText(0), nil, "no more text")
    eq(LiveStatusLogic.actions(s.devices[0], in: s).map(\.command), ["close", "open"], "at most two buttons")
    eq(LiveStatusLogic.actions(s.devices[2], in: s), [], "no buttons on an inactive device")
    var over = s
    over.active = false
    eq(LiveStatusLogic.actions(s.devices[0], in: over), [], "no buttons in the final state")
    eq(LiveStatusLogic.lead(s)?.id, "cover.garage", "lead device")
    eq(LiveStatusLogic.compactText(s), "5 live", "compact: several")
    let one = LiveStatusState(title: "", subtitle: nil, since: 0, active: true, devices: [s.devices[1]], more: 0)
    eq(LiveStatusLogic.compactText(one), "Unlocked", "compact: one device's state")
    eq(LiveStatusLogic.title(one, fallback: "Front door unlocked"), "Front door unlocked", "title from the attributes")
    eq(LiveStatusLogic.title(one, fallback: " "), "Front door", "title from the device")
    eq(LiveStatusLogic.link(s), "newman://device?id=cover.garage", "tap opens the device")
    eq(LiveStatusLogic.link(LiveStatusState(title: "x", subtitle: nil, since: 0, active: true, devices: [], more: 4)),
       "newman://open", "tap opens the app")

    // Token bodies (POST / DELETE /api/live/tokens).
    eq(LiveStatusLogic.hexToken(Data([0x0a, 0xff, 0x00])), "0aff00", "hex token")
    eq(LiveStatusLogic.pushToStartBody(token: "ab", deviceId: "p1", sandbox: false),
       ["kind": .string("push-to-start"), "token": .string("ab"), "deviceId": .string("p1")], "push-to-start body")
    eq(LiveStatusLogic.pushToStartBody(token: "ab", deviceId: "p1", sandbox: true)["apnsEnvironment"], .string("sandbox"), "sandbox")
    eq(LiveStatusLogic.updateBody(activityKey: "k1", token: "cd", deviceId: "p1", sandbox: false),
       ["kind": .string("update"), "activityKey": .string("k1"), "token": .string("cd"), "deviceId": .string("p1")], "update body")
    eq(LiveStatusLogic.endBody(activityKey: "k1", deviceId: "p1"),
       ["kind": .string("update"), "activityKey": .string("k1"), "deviceId": .string("p1")], "end body")

    // Matching the hub.
    let plan = LiveStatusLogic.plan(local: [LiveStatusLocal(id: "A", key: "k1"), LiveStatusLocal(id: "B", key: "gone"),
                                            LiveStatusLocal(id: "C", key: "k1")],
                                    current: ["k1", "k2", "k2"])
    eq(plan, LiveStatusPlan(end: ["B", "C"], start: ["k2"], update: ["k1": "A"]), "end stale + duplicate, start missing")
    eq(LiveStatusLogic.plan(local: [LiveStatusLocal(id: "A", key: "k1")], current: []),
       LiveStatusPlan(end: ["A"]), "hub shows nothing: end")

    // The hub's reply: {"attributesType", "max", "activities": [{key, slot, title, contentState, attributes}]}.
    let current = #"""
    {"attributesType":"NewmanLiveAttributes","max":5,"activities":[
     {"key":"cover.garage.1760000000","slot":"cover.garage","title":"Garage open","contentState":\#(stateJSON),
      "attributes":{"key":"cover.garage.1760000000","title":"Garage open"}},
     {"attributes":{"key":"k2","title":"2 more"},"content-state":{"title":"2 more","active":true,"devices":[],"more":2}},
     {"activityKey":"k3","state":{"title":"x"}},
     {"key":"bad","contentState":"nope"}, {"title":"no key","contentState":{}}]}
    """#
    let items = LiveStatusLogic.parseCurrent(Data(current.utf8)) ?? []
    eq(items.map(\.key), ["cover.garage.1760000000", "k2", "k3"], "current keys")
    eq(LiveStatusLogic.currentPath(deviceId: "iph c&1"), "api/live/current?deviceId=iph%20c%261", "current path")
    eq(items.first?.state, s, "current state")
    eq(items.dropFirst().first?.title, "2 more", "title from attributes")
    eq(items.last?.title, "x", "title from the state")
    eq(LiveStatusLogic.parseCurrent(Data(#"[{"key":"k","state":{}}]"#.utf8))?.map(\.key), ["k"], "bare list")
    eq(LiveStatusLogic.parseCurrent(Data(#"{"activities":[]}"#.utf8))?.count, 0, "nothing current")
    check(LiveStatusLogic.parseCurrent(Data(#"{"error":"nope"}"#.utf8)) == nil, "not a list: leave activities alone")
    check(LiveStatusLogic.parseCurrent(Data("<html>".utf8)) == nil, "not JSON")
}
