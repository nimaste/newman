import Foundation

// The phone's Home tab, Lights by room, Climate bars and the automation editor's draft
// (Shared/Core/HomeModel.swift, AutomationDraft.swift). Called from main.swift.

func runHomeTests() {
    let homeJSON = #"""
    {"cards":[{"id":"cameras","on":true,"title":"Cameras","value":"","detail":"Person · 8:36 AM","tone":"normal","camera":"frigate.camera_garage_side"},
              {"id":"climate","on":true,"title":"Climate","value":"21.8°","detail":"Heat · set to 22°","tone":"warm"},
              {"id":"appliances","on":false,"title":"Appliances","value":"","detail":""},
              {"id":"lights","title":"Lights","value":"1 on","detail":"Kitchen Under Cabinet"}],
     "favourites":["group.kitchen_under_cabinet","meross.garage"],
     "status":{"level":"danger","summary":"Garage open","danger":[{"id":"garage","title":"Garage door open","detail":"For 15 minutes","entity":"meross.garage","action":{"command":"close","label":"Close"}}],
               "warnings":[{"entity":"ring.md","issue":"battery_low","text":"Motion Detector · Battery low"}]},
     "cameras":{"mode":"featured","featured":"frigate.camera_doorbell","top":{"camera":"frigate.camera_garage_side","label":"Person","zone":"Garage Walkway","at":1792400100}},
     "climate":{"thermostat":"homekit.thermostat_1","rooms":[{"entity":"matter.up","name":"Upper Floor","value":22.2}],"range":[18,26],
                "bands":[{"name":"Cool","below":21.5,"color":"#9db8ff"},{"name":"Comfortable","color":"#f5b14c"},{"name":"Warm","above":22.0,"color":"#ff9a4d"}]},
     "canAutomate":true}
    """#
    let home = try? JSONDecoder().decode(HomeData.self, from: Data(homeJSON.utf8))
    check(home != nil, "home decodes")
    eq(home?.shownCards.map { $0.id } ?? [], ["cameras", "climate", "lights"], "cards: the ones on, in order")
    eq(home?.cards.last?.on ?? false, true, "a card without on: on")
    eq(home?.status.danger.first?.action?.label ?? "", "Close", "danger action")
    eq(home?.status.warnings.first?.id ?? "", "ring.md/battery_low", "warning id")
    eq(home?.cameras.label ?? "", "Featured camera", "featured mode: its label")
    eq(home?.cameras.bigCamera ?? "", "frigate.camera_doorbell", "featured: the chosen camera is big")
    eq(home?.canAutomate ?? false, true, "canAutomate")
    eq(HomeData.Cameras(mode: "latest", featured: "x", top: HomeData.TopCamera(camera: "cam.a")).bigCamera ?? "", "cam.a",
       "latest: the latest detection's camera")
    eq(HomeData.Cameras().label, "Latest activity", "latest label")
    // An older or partial reply still decodes, with the default bands and range.
    let bare = try? JSONDecoder().decode(HomeData.self, from: Data(#"{"climate":{"range":[30,20]}}"#.utf8))
    eq(bare?.climate.range ?? [], HomeLogic.defaultRange, "a backwards range: the default")
    eq(bare?.climate.bands.count ?? 0, 3, "no bands: the defaults")
    eq(bare?.status.level ?? "", "ok", "no status: ok")

    // Bars and colours: the house's own range and bands.
    let bands = home?.climate.bands ?? []
    eq(HomeLogic.bandColor(21.0, bands: bands), 0x9DB8FF, "below 21.5: cool")
    eq(HomeLogic.bandColor(21.8, bands: bands), 0xF5B14C, "in between: comfortable")
    eq(HomeLogic.bandColor(22.2, bands: bands), 0xFF9A4D, "above 22: warm")
    eq(HomeLogic.bandColor(22.0, bands: bands), 0xF5B14C, "exactly 22: still comfortable")
    eq(HomeLogic.barFraction(22, range: [18, 26]), 0.5, "the middle of the range")
    eq(HomeLogic.barFraction(10, range: [18, 26]), 0, "below the range: empty")
    eq(HomeLogic.barFraction(40, range: [18, 26]), 1, "above the range: full")
    eq(HomeLogic.hex("#FF9a4d"), 0xFF9A4D, "hex colour")
    check(HomeLogic.hex("orange") == nil, "not a colour")

    // Edit Home and Rooms on Climate bodies.
    let body = HomeLogic.editHomeBody(cards: [HomeData.Card(id: "lights", on: false)], favourites: ["a", "b", "c", "d", "e"])
    eq(body["favourites"]?.arrayValue?.count ?? 0, 4, "at most 4 favourites")
    eq(body["cards"]?.arrayValue?.first?["on"]?.boolValue ?? true, false, "a card switched off")
    eq(HomeLogic.climateBody([HomeData.Room(entity: "m.1", name: "Upper", value: nil)])["rooms"]?.arrayValue?.first?["name"]?.stringValue ?? "",
       "Upper", "room name sent")

    // Lights by room: a group is one row, its lights tucked inside.
    let lightKind = try? JSONDecoder().decode(Kind.self, from: Data(#"""
    {"id":"light","category":"lighting","activeState":"on","states":[{"id":"on","label":"On"},{"id":"off","label":"Off"}],
     "commands":[{"id":"turn_on","label":"Turn on","confirmsTo":"on"},{"id":"turn_off","label":"Turn off","confirmsTo":"off"},
                 {"id":"set_brightness","label":"Brightness","valueType":"percent","takesValue":true,"confirmsTo":"on"}]}
    """#.utf8))
    let remoteKind = try? JSONDecoder().decode(Kind.self, from: Data(#"{"id":"remote","category":"lighting","states":[{"id":"idle","label":"Idle"}]}"#.utf8))
    func ent(_ json: String) -> Entity? { try? JSONDecoder().decode(Entity.self, from: Data(json.utf8)) }
    let ents = [
        ent(#"{"id":"group.kuc","name":"Kitchen Under Cabinet","kind":"light","state":"on","room":"Kitchen","attrs":{"members":["z.1","z.2","z.3"],"brightness":1}}"#),
        ent(#"{"id":"z.1","name":"Under Cabinet - Coffee","kind":"light","state":"on","group":"group.kuc"}"#),
        ent(#"{"id":"z.2","name":"Under Cabinet - Kitchen Work","kind":"light","state":"on","room":"Kitchen"}"#),
        ent(#"{"id":"z.3","name":"Under Cabinet - Kitchenette","kind":"light","state":"on","group":"group.kuc"}"#),
        ent(#"{"id":"l.kitchen","name":"Kitchen","kind":"light","state":"off","attrs":{"area":"Kitchen"}}"#),
        ent(#"{"id":"l.powder","name":"Powder Room","kind":"light","state":"off","room":"Powder Room"}"#),
        ent(#"{"id":"l.loose","name":"Lamp","kind":"light","state":"off"}"#),
        ent(#"{"id":"r.pico","name":"Kitchen Pico","kind":"remote","state":"idle","room":"Kitchen"}"#),
    ].compactMap { $0 }
    eq(ents.count, 8, "light fixtures decode")
    eq(ents.first?.room ?? "", "Kitchen", "room decodes")
    eq(ents[1].group ?? "", "group.kuc", "group decodes")
    var kinds: [String: Kind] = [:]
    if let lightKind { kinds["light"] = lightKind }
    if let remoteKind { kinds["remote"] = remoteKind }
    let rooms = LightRooms.rooms(ents, kinds: kinds, screens: ["lighting": "lights"])
    eq(LightRooms.rooms(ents, kinds: kinds, screens: ["lighting": "security"]).count, 0, "only categories the hub shows on Lights")
    eq(rooms.map { $0.name }, ["Kitchen", "Powder Room", "Other"], "rooms by name, Other last")
    eq(rooms.first?.rows.map { $0.id } ?? [], ["group.kuc", "l.kitchen"], "the group first, its lights hidden, remotes left out")
    eq(rooms.first?.rows.first?.members ?? [], ["z.1", "z.2", "z.3"], "the group's lights behind it")
    let byId = Dictionary(ents.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    eq(rooms.first.map { LightRooms.roomLine($0, entities: byId, kinds: kinds) } ?? "", "Kitchen Under Cabinet on", "one on: its name")
    eq(LightRooms.onCount(rooms, entities: byId, kinds: kinds), 1, "a group counts once")
    eq(rooms.dropFirst().first.map { LightRooms.roomLine($0, entities: byId, kinds: kinds) } ?? "", "All off", "all off")

    // Temperature sensors for Rooms on Climate: any device with a ° reading.
    let sensors = HomeLogic.temperatureSensors([
        ent(#"{"id":"m.up","name":"Temp Sensor - Upper Floor","kind":"temperature","properties":[{"id":"temperature","type":"number","unit":"°C","value":22.19}]}"#),
        ent(#"{"id":"m.hum","name":"Humidity","kind":"sensor","properties":[{"id":"humidity","type":"number","unit":"%","value":40}]}"#),
    ].compactMap { $0 })
    eq(sensors.map { $0.id }, ["m.up"], "only temperature readings")

    // The editor's draft: the real "Turn on Under Cabinet Lights", edited and sent back whole.
    let autoJSON = #"""
    {"name":"Turn on Under Cabinet Lights","enabled":true,"summary":"At 6:20 AM…","lastRun":{"id":"r1"},
     "trigger":{"type":"time","items":[{"type":"time","time":"06:20","days":[0,1,2,3,4,5,6]},{"type":"sun","event":"sunset","offset":-30}],
                "match":"any","within":{"amount":2,"unit":"minutes"},"entities":[],"time":"06:20","days":[0,1,2,3,4,5,6]},
     "condition":{"mode":"any","entities":[],"between":{"from":"22:00","to":"06:00"},"days":null,"sun":null,"dates":null,"devices":[]},
     "actions":[{"entities":["mqtt.a","mqtt.b"],"command":"set_brightness","value":1,"for":{"amount":105,"unit":"minutes"}},{"notify":"x"}],
     "notify":{"enabled":false,"title":"","buttons":[]},"followUp":{"enabled":false,"remindTimes":3},"id":"auto_702def18"}
    """#
    let raw = (try? JSONDecoder().decode([String: JSONValue].self, from: Data(autoJSON.utf8))) ?? [:]
    var draft = AutomationDraft(raw: raw)
    eq(draft.id ?? "", "auto_702def18", "draft id")
    check(draft.raw["summary"] == nil && draft.raw["lastRun"] == nil, "list-only fields dropped")
    eq(draft.triggerItems.count, 2, "two triggers")
    eq(draft.triggerItems.map { $0.isEditable }, [true, false], "the sun trigger is kept, read-only")
    eq(draft.triggerItems.first?.time ?? "", "06:20", "time")
    eq(TriggerItem.daysText(draft.triggerItems.first?.days ?? []), "Every day", "every day")
    eq(draft.actions.first?.forMinutes ?? 0, 105, "for 105 minutes")
    eq(draft.actions.map { $0.isEditable }, [true, false], "an action the phone can't edit is kept")
    eq(draft.otherConditions, ["At certain times"], "a web-only condition named")
    var items = draft.triggerItems
    items[0].time = "06:30"
    items[0].days = [0, 1, 2, 3, 4]
    draft.triggerItems = items
    draft.cooldownMinutes = 5
    var acts = draft.actions
    acts[0].value = .number(10)
    acts[0].forMinutes = nil
    draft.actions = acts
    draft.conditionDevices = [ConditionDevice.new("meross.garage", state: "closed")]
    let saved = draft.body
    eq(saved["trigger"]?["items"]?.arrayValue?.count ?? 0, 2, "both triggers saved")
    eq(saved["trigger"]?["items"]?.arrayValue?.last?["type"]?.stringValue ?? "", "sun", "the sun trigger saved untouched")
    eq(saved["trigger"]?["items"]?.arrayValue?.last?["offset"]?.doubleValue ?? 0, -30, "its own fields too")
    check(saved["trigger"]?["time"] == nil && saved["trigger"]?["type"] == nil, "flat copies left for the hub to work out")
    eq(saved["trigger"]?["within"]?["amount"]?.doubleValue ?? 0, 2, "the trigger's other settings kept")
    eq(saved["notify"]?["remindTimes"] == nil, true, "notify untouched")
    eq(saved["followUp"]?["remindTimes"]?.doubleValue ?? 0, 3, "follow-up untouched")
    eq(saved["actions"]?.arrayValue?.last?["notify"]?.stringValue ?? "", "x", "an unknown action kept")
    check(saved["actions"]?.arrayValue?.first?["for"] == nil, "put it back: off")
    eq(saved["cooldownMinutes"]?.doubleValue ?? 0, 5, "don't repeat for 5 minutes")
    eq(saved["condition"]?["between"]?["from"]?.stringValue ?? "", "22:00", "a web-only condition kept")
    eq(saved["condition"]?["entities"]?.arrayValue?.count ?? 0, 1, "condition entities mirrored")
    eq(TriggerItem.daysText([0, 1, 2, 3, 4]), "Weekdays", "weekdays")
    eq(TriggerItem.daysText([5, 6]), "Weekends", "weekends")
    eq(TriggerItem.daysText([0, 2]), "Mon, Wed", "some days")
    eq(TriggerItem.hourMinute("06:20").map { "\($0.0):\($0.1)" } ?? "", "6:20", "parse a time")
    check(TriggerItem.hourMinute("25:00") == nil, "not a time")
    eq(TriggerItem.hhmm(7, 5), "07:05", "format a time")
    draft.cooldownMinutes = nil
    check(draft.body["cooldownMinutes"]?.isNull ?? false, "default: null")
    eq(AutomationDraft.cooldownText(nil), "Default", "null: Default")
    draft.cooldownMinutes = 0
    eq(draft.body["cooldownMinutes"]?.doubleValue ?? -1, 0, "no wait: 0")
    eq(draft.cooldownMinutes, 0, "no wait reads back 0")
    eq(AutomationDraft.cooldownText(0), "No wait", "0: No wait")
    eq(AutomationDraft.cooldownText(60), "60 min", "an hour")
    eq(AutomationDraft.cooldownChoices.count, 7, "Default, No wait, 1, 5, 15, 30, 60")
    eq(TriggerItem.displayDays.map { TriggerItem.dayLetters[$0] }.joined(), "SMTWTFS", "chips Sunday first")

    // A new automation, and an older one with only the flat trigger fields.
    let fresh = AutomationDraft.new()
    check(fresh.isNew, "new: no id")
    eq(fresh.triggerItems.first?.time ?? "", "07:00", "new: 7:00")
    eq(fresh.actions.count, 0, "new: nothing happens yet")
    let flat = AutomationDraft(raw: ["trigger": .object(["type": .string("state"), "entities": .array([.string("x")]), "to": .string("open")])])
    eq(flat.triggerItems.first?.to ?? "", "open", "flat trigger read as one item")
    var ev = TriggerItem.newEvent("r.pico", event: "button_1")
    ev.entities = ["r.other"]
    eq(ev.raw["entity"]?.stringValue ?? "", "r.other", "an event trigger's device")
    eq(Durations.minutes(.object(["amount": .number(2), "unit": .string("hours")])) ?? 0, 120, "hours in minutes")

    // A notification's alert id.
    eq(PushPayload.alertId(userInfo: ["alertId": "254"]) ?? "", "254", "alertId text")
    eq(PushPayload.alertId(userInfo: ["alertId": 255]) ?? "", "255", "alertId number")
    check(PushPayload.alertId(userInfo: [:]) == nil, "no alertId")
}
