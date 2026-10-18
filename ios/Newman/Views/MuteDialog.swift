import SwiftUI

/// One problem on one device, about to be muted.
struct MuteTarget: Identifiable, Equatable {
    var entityId: String
    var device: String
    var issue: String
    var text: String
    var id: String { entityId + "/" + issue }
}

/// "Mute "No AC power"?" with For 1 day / For 1 week / Until it's fixed / Always / Cancel.
struct MuteDialog: ViewModifier {
    @EnvironmentObject var model: AppModel
    @Binding var target: MuteTarget?

    func body(content: Content) -> some View {
        content.confirmationDialog(target.map { "Mute “\($0.text)”?" } ?? "Mute",
                                   isPresented: Binding(get: { target != nil }, set: { if !$0 { target = nil } }),
                                   titleVisibility: .visible, presenting: target) { t in
            ForEach(MuteLogic.choices, id: \.mode) { c in
                Button(c.label) { Task { await model.mute(t.entityId, issue: t.issue, mode: c.mode) } }
            }
            Button("Cancel", role: .cancel) {}
        } message: { t in
            Text("\(t.device). Its other problems still show.")
        }
    }
}

extension View {
    func muteDialog(_ target: Binding<MuteTarget?>) -> some View { modifier(MuteDialog(target: target)) }
}
