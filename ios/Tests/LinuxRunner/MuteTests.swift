import Foundation

// Muting a device's problem (Shared/Core/MuteLogic.swift, Health in Models.swift). Called from main.swift.

func runMuteTests() {
    func decode(_ json: String) -> Entity? { try? JSONDecoder().decode(Entity.self, from: Data(json.utf8)) }

    let keypad = decode(#"""
    {"id":"ring_activity.kp","name":"Hallway Keypad","kind":"motion","state":"clear",
     "health":{"ok":false,"issues":["Battery low"],
               "items":[{"id":"ac","text":"No AC power","muted":{"mode":"always","until":null,"by":"Chris"}},
                        {"id":"battery_low","text":"Battery low","muted":null},
                        {"id":"tamper","text":"Tamper reported","muted":{"mode":"day","until":1792400100,"by":"Chris"}}]}}
    """#)
    check(keypad != nil, "a device with problem items decodes")
    eq(keypad?.issues ?? [], ["Battery low"], "issues: the unmuted ones, as the hub sends them")
    eq(keypad.map { MuteLogic.active($0).map { $0.id } } ?? [], ["battery_low"], "active: unmuted items")
    eq(keypad.map { MuteLogic.muted($0).map { $0.id } } ?? [], ["ac", "tamper"], "muted: the muted items, in order")
    eq(keypad.map { MuteLogic.active($0).allSatisfy(MuteLogic.canMute) } ?? false, true, "items with ids can be muted")
    let utc = TimeZone(identifier: "UTC")!
    eq(MuteLogic.mutedText(Health.Muted(mode: "always", until: nil, by: nil)), "Muted always", "always")
    eq(MuteLogic.mutedText(Health.Muted(mode: "fixed", until: nil, by: nil)), "Muted until it’s fixed", "until fixed")
    eq(MuteLogic.mutedText(Health.Muted(mode: "day", until: 1792400100, by: nil), timeZone: utc), "Muted until Monday, Oct 19, 8:55 AM",
       "a day: until a date and time")
    eq(MuteLogic.mutedText(Health.Muted(mode: "week", until: nil, by: nil)), "Muted", "no time: just muted")

    // An older hub: plain issues only - shown, but no mute button.
    let old = decode(#"{"id":"x","name":"Old","kind":"lock","health":{"ok":false,"issues":["Jammed"]}}"#)
    eq(old.map { MuteLogic.active($0).map { $0.text } } ?? [], ["Jammed"], "older hub: issues shown")
    eq(old.map { MuteLogic.active($0).contains(where: MuteLogic.canMute) } ?? true, false, "older hub: can't mute")
    eq(old.map { MuteLogic.muted($0).count } ?? -1, 0, "older hub: nothing muted")
    // Unreadable items don't lose the rest of the health.
    let odd = decode(#"{"id":"y","name":"Odd","kind":"lock","health":{"ok":false,"issues":["Jammed"],"items":"nonsense"}}"#)
    eq(odd?.issues ?? [], ["Jammed"], "unreadable items: issues kept")
    eq(odd.map { MuteLogic.active($0).map { $0.text } } ?? [], ["Jammed"], "unreadable items: falls back to issues")
    // Everything muted: nothing needs attention.
    let quiet = decode(#"{"id":"z","name":"Quiet","kind":"motion","health":{"ok":true,"issues":[],"items":[{"id":"ac","text":"No AC power","muted":{"mode":"always"}}]}}"#)
    eq(quiet.map { DeviceDirectory.needsAttention($0) } ?? true, false, "all muted: not needing attention")
    eq(quiet.map { MuteLogic.active($0).count } ?? -1, 0, "all muted: no active problems")

    eq(MuteLogic.body(issue: "ac", mode: "always"), ["issue": .string("ac"), "for": .string("always")], "mute body")
    let reply = MuteLogic.health(from: Data(#"{"health":{"ok":true,"issues":[],"items":[{"id":"ac","text":"No AC power","muted":{"mode":"fixed"}}]}}"#.utf8))
    eq(reply?.items?.first?.muted?.mode ?? "", "fixed", "the hub's answer: the new health")
    check(MuteLogic.health(from: Data("{}".utf8)) == nil, "an answer without health: nil")
    eq(MuteLogic.choices.map { $0.label }, ["For 1 day", "For 1 week", "Until it’s fixed", "Always"], "the choices, in order")
}
