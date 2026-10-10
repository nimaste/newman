import Foundation

/// The control a command asks for, from its `valueType` (kinds.py Command.value_type):
/// percent -> slider, number -> stepper, state -> state picker, choices -> chips of the
/// device's items, text -> text field, nothing -> a plain button.
enum CommandControl: Equatable {
    case button
    case slider(min: Double, max: Double, step: Double, unit: String)
    case stepper(min: Double, max: Double, step: Double, unit: String)
    case statePicker
    case choices
    case text

    static func forCommand(_ cmd: Command) -> CommandControl {
        switch cmd.resolvedValueType {
        case "percent":
            return .slider(min: cmd.min ?? 0, max: cmd.max ?? 100, step: (cmd.step ?? 0) > 0 ? cmd.step! : 1, unit: cmd.unit ?? "%")
        case "number":
            return .stepper(min: cmd.min ?? -1_000_000, max: cmd.max ?? 1_000_000, step: (cmd.step ?? 0) > 0 ? cmd.step! : 1,
                            unit: cmd.unit ?? "")
        case "state":
            return .statePicker
        case "choices":
            return .choices
        case "text":
            return .text
        case "":
            return (cmd.takesValue ?? false) ? .text : .button
        default:
            // A value type this app doesn't know yet: typing the value is always possible.
            return .text
        }
    }

    /// The starting value for a slider or stepper: the device's current value, else the
    /// command's default, else the middle of the range.
    static func startValue(_ e: Entity, _ cmd: Command) -> Double {
        if let v = DeviceLogic.commandCurrent(e, cmd), v.stringValue == nil, let n = v.doubleValue { return n }
        if let d = cmd.defaultValue?.doubleValue { return d }
        let lo = cmd.min ?? 0, hi = cmd.max ?? 100
        return (lo + hi) / 2
    }

    /// Snaps a control value to the command's step and range, as the value to send.
    static func snapped(_ v: Double, _ cmd: Command) -> Double {
        let step = (cmd.step ?? 0) > 0 ? cmd.step! : 1
        var out = (v / step).rounded() * step
        if let lo = cmd.min { out = Swift.max(lo, out) }
        if let hi = cmd.max { out = Swift.min(hi, out) }
        // Keep 0.5 steps from turning into 21.499999.
        return (out * 1000).rounded() / 1000
    }

    /// The JSON value for a number: whole numbers as integers (the hub accepts both).
    static func jsonNumber(_ v: Double) -> JSONValue { .number(v) }
}
