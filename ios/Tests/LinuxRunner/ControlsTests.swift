import Foundation

// A remote's Controls on its entity (Shared/Core/Models.swift RemoteControls). Called from main.swift.

func runControlsTests() {
    func decode(_ json: String) -> Entity? { try? JSONDecoder().decode(Entity.self, from: Data(json.utf8)) }

    let pico = decode(#"""
    {"id":"lutron.remote_14","name":"Kitchen Under Cabinet","kind":"remote","state":"idle",
     "attrs":{"events":["button_1","button_3"],"buttons":[{"id":"button_1","label":"Top","role":"on"}]},
     "controls":{"lights":["mqtt.a","mqtt.b","mqtt.c"],"onLevel":60,"hold":true,
                 "rows":[{"key":"button_1","label":"Tap top","does":"On at 60%","custom":false},
                         {"key":"button_3_long","label":"Hold bottom","does":"Turn off Kitchen","custom":true}]}}
    """#)
    check(pico != nil, "a remote with controls decodes")
    eq(pico?.controls?.lights ?? [], ["mqtt.a", "mqtt.b", "mqtt.c"], "controls: lights")
    eq(pico?.controls?.onLevel ?? -1, 60, "controls: on level")
    eq(pico?.controls?.hold ?? false, true, "controls: hold")
    eq(pico?.controls?.rows.map { $0.label } ?? [], ["Tap top", "Hold bottom"], "controls: rows in order")
    eq(pico?.controls?.rows.last?.custom ?? false, true, "controls: a custom row")
    eq(pico?.controls?.heading ?? "", "Controls · 3 lights", "controls heading, several lights")

    let absent = decode(#"{"id":"light.k","name":"Kitchen","kind":"light","state":"on"}"#)
    check(absent != nil && absent?.controls == nil, "no controls: nil")
    let null = decode(#"{"id":"lutron.remote_7","name":"Pico","kind":"remote","controls":null}"#)
    check(null != nil && null?.controls == nil, "controls null: nil, the entity still decodes")
    let odd = decode(#"{"id":"r","name":"R","kind":"remote","controls":{"onLevel":null,"rows":[{"label":"Tap"}]}}"#)
    eq(odd?.controls?.lights ?? ["x"], [], "controls without lights: none")
    eq(odd?.controls?.onLevel == nil, true, "on level null: what they had last")
    eq(odd?.controls?.rows.first?.key ?? "", "Tap", "a row without a key uses its label")
    eq(odd?.controls?.heading ?? "", "Controls", "no lights: just Controls")
    let bad = decode(#"{"id":"r2","name":"R2","kind":"remote","controls":"nonsense"}"#)
    check(bad != nil && bad?.controls == nil, "unreadable controls don't lose the entity")
    let one = decode(#"{"id":"r3","name":"R3","kind":"remote","controls":{"lights":["l1"]}}"#)
    eq(one?.controls?.heading ?? "", "Controls · 1 light", "one light")
}
