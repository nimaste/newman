import SwiftUI

/// More › Automations: every automation the house has, most recently run first, with the hub's own
/// summary, when it last ran and a switch. Building and editing them stays on the web dashboard;
/// only admin accounts may switch or run them (others see the hub's refusal as a toast).
struct AutomationsScreen: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let list = AutomationList.sorted(model.automations ?? [])
        return ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Automations").font(AppFont.sora(28)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        if let all = model.automations {
                            Text(AutomationList.headerLine(all, now: Date()))
                                .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                        }
                    }
                    if model.automations == nil {
                        ProgressView().tint(Theme.text).frame(maxWidth: .infinity).padding(.top, 30)
                    } else if list.isEmpty {
                        Text("No automations yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 20)
                    } else {
                        LazyVStack(spacing: 0) {
                            ForEach(Array(list.enumerated()), id: \.element.id) { i, a in
                                AutomationRow(automation: a)
                                if i < list.count - 1 { Divider().overlay(Theme.divider) }
                            }
                        }
                        .glassCard(padding: 14)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await model.reloadAutomations() }
        }
        .task { await model.reloadAutomations() }
    }
}

/// An automation's switch, sent at once (put back if the hub refuses).
private func automationSwitch(_ model: AppModel, _ a: AutomationSummary) -> some View {
    Toggle(isOn: Binding(
        get: { a.enabled },
        set: { on in Task { await model.setAutomation(a.id, enabled: on) } }
    )) { EmptyView() }
    .labelsHidden()
    .toggleStyle(SwitchToggleStyle(tint: Theme.blue))
    .accessibilityLabel(a.name)
    .accessibilityValue(a.enabled ? "On" : "Off")
}

struct AutomationRow: View {
    @EnvironmentObject var model: AppModel
    let automation: AutomationSummary

    var body: some View {
        let a = automation
        let line = AutomationList.lastRunLine(a.lastRun, now: Date())
        HStack(alignment: .top, spacing: 12) {
            NavigationLink(value: MoreRoute.automation(a.id)) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(a.name).font(AppFont.manrope(15, .heavy)).foregroundColor(a.enabled ? Theme.text : Theme.muted)
                        .multilineTextAlignment(.leading)
                    if let s = a.summary, !s.isEmpty {
                        Text(s).font(AppFont.manrope(13, .semibold)).foregroundColor(Theme.muted)
                            .lineLimit(3).multilineTextAlignment(.leading)
                    }
                    Text(a.enabled ? line : "Off · " + line)
                        .font(AppFont.manrope(12, .heavy))
                        .foregroundColor(a.lastRun?.startedAt == nil || !a.enabled ? Theme.dim : Theme.teal)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Shows its runs and Run now")
            automationSwitch(model, a)
                .padding(.top, 2)
        }
        .padding(.vertical, 12)
    }
}

/// One automation: what it does, on or off, its recent runs, and Run now.
struct AutomationDetailScreen: View {
    @EnvironmentObject var model: AppModel
    let automationId: String
    @State private var runs: [AutomationRun]?
    @State private var running = false

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                if let a = model.automations?.first(where: { $0.id == automationId }) {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(a.name).font(AppFont.sora(26)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        VStack(alignment: .leading, spacing: 12) {
                            if let s = a.summary, !s.isEmpty {
                                Text(s).font(AppFont.manrope(15, .semibold)).foregroundColor(Theme.text)
                            }
                            HStack {
                                Text(a.enabled ? "On" : "Off").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                                Spacer()
                                automationSwitch(model, a)
                            }
                            Button {
                                running = true
                                Task {
                                    if await model.runAutomation(a) { await loadRuns() }
                                    running = false
                                }
                            } label: {
                                HStack(spacing: 8) {
                                    if running { ProgressView().tint(Theme.background) } else { Image(systemName: "play.fill") }
                                    Text("Run now")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(PillButtonStyle(height: 48, prominent: true))
                            .disabled(running)
                            .accessibilityHint("Its conditions are still checked")
                        }
                        .glassCard(padding: 14)

                        VStack(alignment: .leading, spacing: 0) {
                            CardHeader(text: "Recent runs").padding(.bottom, 4)
                            if let runs {
                                if runs.isEmpty {
                                    Text("It hasn't run yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                                        .padding(.vertical, 8)
                                }
                                ForEach(Array(runs.enumerated()), id: \.offset) { i, r in
                                    HStack {
                                        Text(r.startedAt.map { AutomationList.when(Date(timeIntervalSince1970: $0), now: Date()).capitalizedFirst } ?? "—")
                                            .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.text)
                                        Spacer()
                                        Text(AutomationList.statusLabel(r.status))
                                            .font(AppFont.manrope(13, .heavy))
                                            .foregroundColor(r.status == "failed" ? Theme.coralText : Theme.muted)
                                    }
                                    .frame(minHeight: 40)
                                    .accessibilityElement(children: .combine)
                                    if i < runs.count - 1 { Divider().overlay(Theme.divider) }
                                }
                            } else {
                                ProgressView().tint(Theme.text).frame(maxWidth: .infinity).padding(.vertical, 12)
                            }
                        }
                        .glassCard(padding: 14)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 32)
                } else {
                    Text("This automation is gone.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(30)
                }
            }
            .refreshable {
                await model.reloadAutomations()
                await loadRuns()
            }
        }
        .task { await loadRuns() }
    }

    private func loadRuns() async {
        if let r = try? await APIClient.shared.automationRuns(automationId) { runs = r } else if runs == nil { runs = [] }
    }
}

private extension String {
    /// "today 2:54 PM" -> "Today 2:54 PM".
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
