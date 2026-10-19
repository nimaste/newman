import SwiftUI

/// A category without a purpose-built screen (its Home card's `screen` is nil - any category, built in or
/// made in Newman): its devices, needing attention first, each a Devices row with its quick control;
/// tapping one opens everything it can do.
struct CategoryScreen: View {
    @EnvironmentObject var model: AppModel
    let category: String

    var body: some View {
        let devices = CategoryLogic.devices(category, entities: model.allEntities, kinds: model.kinds)
        let title = CategoryLogic.name(category, categories: model.categoryList)
        return ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(AppFont.sora(28)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Text(DeviceDirectory.countLine(devices))
                            .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted)
                    }
                    if devices.isEmpty {
                        Text("Nothing here.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 20)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(devices.enumerated()), id: \.element.id) { i, e in
                                DirectoryRow(entityId: e.id)
                                if i < devices.count - 1 { Divider().overlay(Theme.divider) }
                            }
                        }
                        .glassCard(padding: 14)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await model.reloadAll() }
        }
    }
}
