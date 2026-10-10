import AppIntents
import Foundation

/// A Newman device in widget / control / Shortcuts pickers. The list is ALL of the hub's devices a
/// widget can show (WidgetLogic.isShowable - from the kind metadata), grouped by room, then by
/// type, with search. Compiled into the app (Siri / Shortcuts) and the widget extension.
struct DeviceEntity: AppEntity, Identifiable, Hashable {
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "Device"
    static var defaultQuery = DeviceQuery()

    var id: String
    var name: String
    /// "Kitchen · Light"
    var detail: String
    /// The hub's icon name (Symbols maps it to an SF Symbol).
    var icon: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: LocalizedStringResource(stringLiteral: name),
                              subtitle: detail.isEmpty ? nil : LocalizedStringResource(stringLiteral: detail),
                              image: .init(systemName: Symbols.name(icon)))
    }

    init(id: String, name: String, detail: String = "", icon: String = "dot") {
        self.id = id
        self.name = name
        self.detail = detail
        self.icon = icon
    }

    init(_ item: WidgetHub.CatalogItem) {
        self.init(id: item.id, name: item.name, detail: item.detail, icon: item.icon)
    }
}

struct DeviceQuery: EntityStringQuery {
    init() {}

    /// The devices a saved widget / control / shortcut names. Never drops one: offline, the last
    /// list the phone saw (or just the id) stands in, so a widget doesn't lose its setup.
    func entities(for identifiers: [DeviceEntity.ID]) async throws -> [DeviceEntity] {
        var items = WidgetHub.cachedCatalog
        if identifiers.contains(where: { id in !items.contains { $0.id == id } }) {
            items = await WidgetHub.catalog()
        }
        return identifiers.map { id in
            items.first { $0.id == id }.map { DeviceEntity($0) } ?? DeviceEntity(id: id, name: id)
        }
    }

    func entities(matching string: String) async throws -> IntentItemCollection<DeviceEntity> {
        Self.collection(await WidgetHub.catalog(), query: string)
    }

    func suggestedEntities() async throws -> IntentItemCollection<DeviceEntity> {
        Self.collection(await WidgetHub.catalog(), query: "")
    }

    /// Sections in the catalog's order (rooms A-Z, then types), filtered by the search text.
    static func collection(_ items: [WidgetHub.CatalogItem], query: String) -> IntentItemCollection<DeviceEntity> {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let shown = q.isEmpty ? items : items.filter {
            $0.name.localizedCaseInsensitiveContains(q) || $0.detail.localizedCaseInsensitiveContains(q)
        }
        var order: [String] = []
        var bySection: [String: [WidgetHub.CatalogItem]] = [:]
        for i in shown {
            if bySection[i.section] == nil { order.append(i.section) }
            bySection[i.section, default: []].append(i)
        }
        let sections = order.map { title in
            IntentItemSection<DeviceEntity>(
                LocalizedStringResource(stringLiteral: title),
                items: (bySection[title] ?? []).map { item in
                    IntentItem<DeviceEntity>(DeviceEntity(item), title: LocalizedStringResource(stringLiteral: item.name),
                                             subtitle: item.detail.isEmpty ? nil : LocalizedStringResource(stringLiteral: item.detail),
                                             image: .init(systemName: Symbols.name(item.icon)))
                })
        }
        return IntentItemCollection<DeviceEntity>(sections: sections)
    }
}
