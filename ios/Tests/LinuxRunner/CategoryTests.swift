import Foundation

// Categories, dynamic Home cards, icons and the Security sections (Shared/Core/Categories.swift,
// HomeModel.swift, IconNames.swift, DeviceLogic.icon). Called from main.swift.

func runCategoryTests() {
    func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T? { try? JSONDecoder().decode(type, from: Data(json.utf8)) }
    func ent(_ json: String) -> Entity? { decode(Entity.self, json) }
    func kind(_ json: String) -> Kind? { decode(Kind.self, json) }

    // Home cards straight from /api/home: any category, its screen, NEW, who may edit.
    let home = decode(HomeData.self, #"""
    {"cards":[
      {"id":"lighting","on":true,"title":"Lights","category":"lighting","screen":"lights","value":"2 on","detail":"Kitchen","tone":"warm"},
      {"id":"cat.pool","on":false,"new":true,"title":"Pool","category":"cat.pool","screen":null,"value":"3 devices","detail":"","tone":"normal"},
      {"id":"garage","on":true,"title":"Garage","category":"garage","screen":"security","value":"Closed","detail":"","tone":"good"},
      {"id":"today","on":true,"title":"Today","category":null,"screen":"today","value":"","detail":"","tone":"normal"}
    ],"favourites":[],"status":{"level":"ok","summary":""},"canEditHome":false,"layout":"household"}
    """#)
    check(home != nil, "dynamic home decodes")
    let cards = home?.cards ?? []
    eq(cards.map { $0.id }, ["lighting", "cat.pool", "garage", "today"], "every card, in order")
    eq(cards.map { $0.role ?? "-" }, ["lights", "-", "security", "today"], "roles from the hub's screen")
    eq(cards.map { $0.new }, [false, true, false, false], "NEW decodes")
    eq(cards[1].category ?? "", "cat.pool", "a custom category's card")
    eq(home?.canEditHome, false, "canEditHome decodes")
    eq(home?.layout ?? "", "household", "layout decodes")
    eq(home?.shownCards.map { $0.id } ?? [], ["lighting", "garage", "today"], "off cards hidden")
    // An older hub: no screen/category, card ids were the screens; no canEditHome means yes.
    let old = decode(HomeData.self, #"{"cards":[{"id":"lights","on":true,"title":"Lights"},{"id":"appliances","on":true,"title":"Appliances"}]}"#)
    eq(old?.cards.map { $0.role ?? "-" } ?? [], ["lights", "-"], "legacy ids still open their screens")
    eq(old?.canEditHome, true, "older hubs: may edit")
    eq(HomeData.Card(id: "lights", on: false).role ?? "", "lights", "legacy id in code")
    check(HomeData.Card(id: "lights", category: "lights").role == nil, "a category that happens to be called lights isn't a role")

    // Categories: GET /api/categories wins over meta's; screens map from it.
    let fetched = decode([CategoryInfo].self, #"""
    [{"id":"security","name":"Security","builtIn":true,"screen":"security","count":4},
     {"id":"garage","name":"Garage","builtIn":true,"screen":"security","count":1},
     {"id":"lighting","name":"Lighting","builtIn":true,"screen":"lights","count":9},
     {"id":"appliances","name":"Appliances","builtIn":true,"screen":null,"count":2},
     {"id":"cat.pool","name":"Pool","builtIn":false,"count":3}]
    """#) ?? []
    eq(fetched.count, 5, "categories decode")
    eq(fetched.last?.builtIn, false, "custom category")
    let metaCats = [MetaCategory(id: "security", name: "Security", screen: "security")]
    eq(CategoryLogic.list([], meta: metaCats).map { $0.id }, ["security"], "older hub: meta's categories")
    let list = CategoryLogic.list(fetched, meta: metaCats)
    eq(list.map { $0.id }, ["security", "garage", "lighting", "appliances", "cat.pool"], "the hub's order")
    let screens = CategoryLogic.screens(list)
    eq(screens, ["security": "security", "garage": "security", "lighting": "lights"], "only categories with a screen")
    eq(CategoryLogic.name("cat.pool", categories: list), "Pool", "custom name")

    // Icons: the override wins (an icon name, or a kind's id → that kind's icon), else the kind's;
    // unknown names get the neutral symbol.
    var kinds: [String: Kind] = [:]
    for k in [
        kind(#"{"id":"lock","name":"Lock","plural":"Locks","category":"security","secureState":"locked","states":[{"id":"locked","label":"Locked"},{"id":"unlocked","label":"Unlocked"}],"commands":[{"id":"lock","label":"Lock","icon":"lock"},{"id":"unlock","label":"Unlock","icon":"unlock"}]}"#),
        kind(#"{"id":"contact","name":"Door sensor","plural":"Door sensors","category":"security","secureState":"closed","states":[{"id":"open","label":"Open"},{"id":"closed","label":"Closed"}]}"#),
        kind(#"{"id":"leak","name":"Leak sensor","plural":"Leak sensors","category":"security","secureState":"dry","states":[{"id":"wet","label":"Wet"},{"id":"dry","label":"Dry"}]}"#),
        kind(#"{"id":"garage_door","name":"Garage door","plural":"Garage doors","category":"garage","secureState":"closed","states":[{"id":"open","label":"Open"},{"id":"closed","label":"Closed"}],"commands":[{"id":"open","label":"Open","icon":"garage"},{"id":"close","label":"Close","icon":"garage"}]}"#),
        kind(#"{"id":"alarm","name":"Alarm","category":"security","isMode":true,"states":[{"id":"home","label":"Home"},{"id":"away","label":"Away"}]}"#),
        kind(#"{"id":"camera","name":"Camera","category":"cameras","hasCamera":true}"#),
        kind(#"{"id":"plug","name":"Plug","category":"appliances","commands":[{"id":"turn_on","label":"Turn on","icon":"power"}]}"#),
        kind(#"{"id":"widget_x","name":"Widget","category":"other"}"#),
        kind(#"{"id":"gizmo","name":"Gizmo","category":"other","unit":"W"}"#),
    ].compactMap({ $0 }) { kinds[k.id] = k }
    eq(kinds.count, 9, "kinds decode")
    eq(DeviceLogic.kindIcon(kinds["lock"]), "lock", "a command's icon")
    eq(DeviceLogic.kindIcon(kinds["contact"]), "contact", "its id when that's an icon name")
    eq(DeviceLogic.kindIcon(kinds["camera"]), "camera", "a camera")
    eq(DeviceLogic.kindIcon(kinds["gizmo"]), "thermometer", "a measurement")
    eq(DeviceLogic.kindIcon(kinds["widget_x"]), "widget_x", "else its id")
    eq(DeviceLogic.kindIcon(nil), "dot", "no kind")
    eq(IconNames.symbol("widget_x"), IconNames.fallback, "an unknown name: the neutral symbol")
    eq(IconNames.symbol(nil), IconNames.fallback, "no name: the neutral symbol")
    eq(IconNames.symbol("Pool"), "figure.pool.swim", "names are case-blind")
    check(IconNames.knows("contact") && !IconNames.knows("widget_x") && !IconNames.knows(""), "knows")
    let plain = ent(#"{"id":"p.1","name":"Kettle","kind":"plug","state":"off"}"#)!
    let pump = ent(#"{"id":"p.2","name":"Pool Pump","kind":"plug","state":"off","icon":"pump","category":"cat.pool"}"#)!
    let asKind = ent(#"{"id":"p.3","name":"Gate","kind":"plug","state":"off","icon":"garage_door"}"#)!
    let blank = ent(#"{"id":"p.4","name":"Blank","kind":"plug","state":"off","icon":"  "}"#)!
    eq(pump.icon ?? "", "pump", "icon decodes")
    check(plain.icon == nil, "no icon: nil")
    eq(DeviceLogic.icon(plain, kinds["plug"], kinds: kinds), "power", "no override: the kind's")
    eq(DeviceLogic.icon(pump, kinds["plug"], kinds: kinds), "pump", "the override")
    eq(DeviceLogic.icon(asKind, kinds["plug"], kinds: kinds), "garage", "a kind's id: that kind's icon")
    eq(DeviceLogic.icon(asKind, kinds["plug"]), "garage_door", "without kinds: the name itself")
    eq(DeviceLogic.icon(blank, kinds["plug"], kinds: kinds), "power", "a blank override is no override")
    eq(Symbols.name(DeviceLogic.icon(asKind, kinds["plug"])), IconNames.fallback, "an unknown name still draws")

    // A generic card's icon: the one most of its devices are drawn with.
    let filter = ent(#"{"id":"p.5","name":"Pool Filter","kind":"plug","state":"off","icon":"pump","category":"cat.pool"}"#)!
    let light = ent(#"{"id":"p.6","name":"Pool Light","kind":"plug","state":"off","category":"cat.pool"}"#)!
    let pool = [plain, pump, filter, light]
    eq(CategoryLogic.mostCommonIcon("cat.pool", entities: pool, kinds: kinds), "pump", "most common icon")
    eq(CategoryLogic.mostCommonIcon("appliances", entities: pool, kinds: kinds), "power", "by effective category")
    eq(CategoryLogic.mostCommonIcon("nothing", entities: pool, kinds: kinds), "grid", "no devices")
    eq(CategoryLogic.devices("cat.pool", entities: pool, kinds: kinds).map { $0.name }, ["Pool Filter", "Pool Light", "Pool Pump"], "a category's devices by name")
    let sections = DeviceDirectory.sections(pool, kinds: kinds, categories: list)
    eq(sections.map { $0.id }, ["appliances", "cat.pool"], "Devices: by effective category in the hub's order")
    eq(sections.last?.title ?? "", "Pool", "a custom category's section")

    // Security: every category the hub shows there (garage too), one section per kind, its plural as the title.
    let house = [
        ent(#"{"id":"l.front","name":"Front Door","kind":"lock","state":"locked"}"#),
        ent(#"{"id":"c.side","name":"Side Door","kind":"contact","state":"closed"}"#),
        ent(#"{"id":"c.back","name":"Back Door","kind":"contact","state":"open"}"#),
        ent(#"{"id":"w.laundry","name":"Laundry","kind":"leak","state":"dry"}"#),
        ent(#"{"id":"g.door","name":"Garage Door","kind":"garage_door","state":"closed"}"#),
        ent(#"{"id":"a.alarm","name":"Alarm","kind":"alarm","state":"home"}"#),
        ent(#"{"id":"cam.1","name":"Porch","kind":"camera","state":"idle","category":"security"}"#),
        ent(#"{"id":"c.shed","name":"Shed Door","kind":"contact","state":"closed","category":"cat.pool"}"#),
        ent(#"{"id":"p.siren","name":"Siren Plug","kind":"plug","state":"off","category":"security"}"#),
    ].compactMap { $0 }
    eq(house.count, 9, "house decodes")
    let members = SecuritySections.members(house, kinds: kinds, screens: screens).map { $0.id }.sorted()
    eq(members, ["c.back", "c.side", "g.door", "l.front", "p.siren", "w.laundry"], "by the category's screen; no alarm, cameras or moved-away devices")
    let secSections = SecuritySections.sections(house, kinds: kinds, screens: screens)
    eq(secSections.map { $0.title }, ["Garage doors", "Locks", "Plug", "Door sensors", "Leak sensors"], "buttons first, then by the kind's plural")
    eq(secSections.map { $0.actionable }, [true, true, true, false, false], "actionable kinds")
    eq(secSections.first { $0.kind == "contact" }?.ids ?? [], ["c.back", "c.side"], "a kind's devices by name")
    eq(SecuritySections.sections(house, kinds: kinds, screens: [:]).count, 0, "no screens known: nothing")
}
