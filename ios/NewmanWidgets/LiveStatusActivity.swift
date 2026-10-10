import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

// Live status as a Live Activity: the Lock Screen banner and the Dynamic Island. The hub starts,
// updates and ends it by push (NewmanLiveAttributes / LiveStatusState); nothing here asks the hub.
// A device row's buttons run RunDeviceActionIntent in this extension, without opening the app; a
// sensitive one (unlock, open the garage…) is refused until the phone is unlocked. Tapping
// elsewhere opens the app on the lead device.

struct LiveStatusActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: NewmanLiveAttributes.self) { context in
            LiveStatusLockScreen(title: LiveStatusLogic.title(context.state, fallback: context.attributes.title),
                                 state: context.state, stale: context.isStale)
                .activityBackgroundTint(Theme.background.opacity(0.92))
                .activitySystemActionForegroundColor(Theme.text)
                .widgetURL(URL(string: LiveStatusLogic.link(context.state)))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    LiveStatusIcon(state: context.state, size: 22)
                        .padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    LiveStatusSince(state: context.state, size: 15)
                        .padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(LiveStatusLogic.title(context.state, fallback: context.attributes.title))
                        .font(.system(size: 15, weight: .bold, design: .rounded))
                        .foregroundColor(Theme.text)
                        .lineLimit(1)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    LiveStatusRows(state: context.state)
                        .padding(.top, 4)
                }
            } compactLeading: {
                LiveStatusIcon(state: context.state, size: 15)
            } compactTrailing: {
                Text(LiveStatusLogic.compactText(context.state))
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundColor(context.state.active ? Theme.amber : Theme.teal)
                    .lineLimit(1)
                    .frame(maxWidth: 72)
            } minimal: {
                LiveStatusIcon(state: context.state, size: 15)
            }
            .widgetURL(URL(string: LiveStatusLogic.link(context.state)))
            .keylineTint(context.state.active ? Theme.amber : Theme.teal)
        }
    }
}

// MARK: views

/// The Lock Screen banner: title (and subtitle), how long it's been, then the device rows.
struct LiveStatusLockScreen: View {
    var title: String
    var state: LiveStatusState
    var stale: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                LiveStatusIcon(state: state, size: 20)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.system(size: 16, weight: .bold, design: .rounded))
                        .foregroundColor(Theme.text)
                        .lineLimit(1)
                    if let sub = state.subtitle, !sub.isEmpty {
                        Text(sub)
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundColor(Theme.muted)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 6)
                LiveStatusSince(state: state, size: 14)
            }
            LiveStatusRows(state: state)
        }
        .padding(14)
        .opacity(stale ? 0.75 : 1)
    }
}

/// The lead device's icon: amber while live, teal once it's over.
struct LiveStatusIcon: View {
    var state: LiveStatusState
    var size: CGFloat

    var body: some View {
        Image(systemName: Symbols.name(LiveStatusLogic.lead(state)?.icon))
            .font(.system(size: size, weight: .semibold))
            .foregroundColor(state.active ? Theme.amber : Theme.teal)
            .accessibilityHidden(true)
    }
}

/// How long it has been live, counted on the phone from `since` (no pushes needed for that).
struct LiveStatusSince: View {
    var state: LiveStatusState
    var size: CGFloat

    var body: some View {
        if state.active && state.since > 0 {
            Text(Date(timeIntervalSince1970: state.since), style: .timer)
                .font(.system(size: size, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .foregroundColor(Theme.muted)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 76, alignment: .trailing)
        }
    }
}

/// The device rows (at most LiveStatusLogic.rowLimit) and "+N more" for the rest.
struct LiveStatusRows: View {
    var state: LiveStatusState

    private var shown: [LiveStatusDevice] { LiveStatusLogic.shown(state) }
    private var moreText: String? { LiveStatusLogic.moreText(LiveStatusLogic.moreCount(state, shown: shown.count)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(shown) { d in
                LiveStatusRow(device: d, state: state)
            }
            if let more = moreText {
                Text(more)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundColor(Theme.muted)
                    .lineLimit(1)
            }
        }
    }
}

/// One device: icon, name, state, and its buttons while it's live.
struct LiveStatusRow: View {
    var device: LiveStatusDevice
    var state: LiveStatusState

    private var live: Bool { state.active && device.active }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: Symbols.name(device.icon))
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(live ? Theme.amber : Theme.teal)
                .frame(width: 20)
                .accessibilityHidden(true)
            Text(device.name)
                .font(.system(size: 14, weight: .bold, design: .rounded))
                .foregroundColor(Theme.text)
                .lineLimit(1)
            if let label = device.stateLabel, !label.isEmpty {
                Text(label)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundColor(live ? Theme.amber : Theme.muted)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            ForEach(LiveStatusLogic.actions(device, in: state), id: \.command) { a in
                Button(intent: RunDeviceActionIntent(device: WidgetDevice(id: device.id, name: device.name),
                                                     action: WidgetAction(command: a.command, label: a.label, sensitive: a.sensitive),
                                                     sensitive: a.sensitive)) {
                    Text(a.label)
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .foregroundColor(Theme.text)
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Capsule().fill(Theme.control))
                        .overlay(Capsule().stroke(Theme.controlBorder, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(a.label) \(device.name)")
            }
        }
        .frame(minHeight: 26)
    }
}
