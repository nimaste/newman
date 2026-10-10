import Foundation

/// Any JSON value. Device attributes, tile settings and command values are open-ended on the
/// hub (new kinds bring new attributes), so they are kept as JSON rather than fixed structs.
enum JSONValue: Codable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([JSONValue].self) {
            self = .array(a)
        } else if let o = try? c.decode([String: JSONValue].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Not a JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .object(let o): try c.encode(o)
        case .array(let a): try c.encode(a)
        case .null: try c.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    /// A number, also when the hub sends it as a numeric string ("21.5").
    var doubleValue: Double? {
        switch self {
        case .number(let n): return n
        case .string(let s): return Double(s.trimmingCharacters(in: .whitespaces))
        default: return nil
        }
    }

    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let a) = self { return a }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let o) = self { return o }
        return nil
    }

    var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    subscript(key: String) -> JSONValue? {
        objectValue?[key]
    }

    /// Plain text for display: strings as-is, whole numbers without ".0".
    var displayText: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return JSONValue.format(n)
        case .bool(let b): return b ? "true" : "false"
        case .null: return ""
        case .array(let a): return a.map { $0.displayText }.joined(separator: ", ")
        case .object: return ""
        }
    }

    static func format(_ n: Double, maxDecimals: Int = 2) -> String {
        if n.rounded() == n && abs(n) < 1e15 { return String(Int(n)) }
        let p = pow(10.0, Double(maxDecimals))
        let r = (n * p).rounded() / p
        if r.rounded() == r { return String(Int(r)) }
        var s = String(format: "%.\(maxDecimals)f", r)
        while s.hasSuffix("0") { s.removeLast() }
        if s.hasSuffix(".") { s.removeLast() }
        return s
    }
}

extension JSONValue {
    init(_ s: String) { self = .string(s) }
    init(_ n: Double) { self = .number(n) }
    init(_ b: Bool) { self = .bool(b) }
}
