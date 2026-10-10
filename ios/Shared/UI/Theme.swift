import SwiftUI

/// The "night glass" look from the design boards (Phone*.dc.html): deep navy with soft blue and
/// teal glows, glass cards, Sora for headings and Manrope for text (bundled, OFL).
enum Theme {
    static let background = Color(hex: 0x0B1026)
    static let cameraBackground = Color(hex: 0x05070F)
    static let text = Color(hex: 0xEEF2FF)
    static let muted = Color(hex: 0xA9B4D6)
    static let dim = Color(hex: 0x8E9AC0)
    static let faint = Color(hex: 0x6F7CA3)
    static let teal = Color(hex: 0x3CCFB4)
    static let blue = Color(hex: 0x7FB8FF)
    static let amber = Color(hex: 0xF5B942)
    static let amberInk = Color(hex: 0x241A05)
    static let coral = Color(hex: 0xFF7A59)
    static let coralText = Color(hex: 0xFFB29E)
    static let red = Color(hex: 0xFF5C5C)
    static let glowBlue = Color(hex: 0x1E3A8A)
    static let glowTeal = Color(hex: 0x0F5E57)

    static let card = Color.white.opacity(0.07)
    static let cardBorder = Color.white.opacity(0.10)
    static let divider = Color.white.opacity(0.08)
    static let control = Color.white.opacity(0.07)
    static let controlBorder = Color.white.opacity(0.14)
    static let pill = Color.white.opacity(0.06)

    static let cardRadius: CGFloat = 22
    static let gap: CGFloat = 10

    /// Off: the look is fixed (the wall, the widgets). The phone app turns it on at launch
    /// (NewmanApp.init): text follows Larger Text (AppFont) and glass cards, pills and chips get
    /// stronger fills and borders with Increase Contrast / Reduce Transparency.
    static var followsAccessibilitySettings = false

    /// Increase Contrast / Reduce Transparency: glass gets an opaque base and a clear outline.
    static let strongCardFill = Color(hex: 0x1A2342)
    static let strongBorder = Color.white.opacity(0.45)
}

/// Whether the glass look should firm up (the phone only; see Theme.followsAccessibilitySettings).
struct StrongGlass {
    static func on(_ contrast: ColorSchemeContrast, _ reduceTransparency: Bool) -> Bool {
        Theme.followsAccessibilitySettings && (contrast == .increased || reduceTransparency)
    }
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: alpha)
    }

    /// "#7FB8FF" (calendar colours from the hub); nil when it isn't a hex colour.
    init?(hexString: String) {
        var s = hexString.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        if s.count == 3 { s = s.map { "\($0)\($0)" }.joined() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(hex: v)
    }
}

/// Bundled fonts (Shared/Resources/Fonts, registered with UIAppFonts). PostScript names.
/// With Theme.followsAccessibilitySettings (the phone) each size scales with Larger Text like the
/// nearest system text style (`textStyle(for:)`) and is exactly the design size at the default
/// setting; otherwise (the wall, the widgets) sizes are fixed. Views that can't grow cap it with
/// `cappedDynamicType()`.
enum AppFont {
    static func sora(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        font(weight == .medium ? "Sora-Medium" : "Sora-SemiBold", size)
    }

    static func manrope(_ size: CGFloat, _ weight: Font.Weight = .bold) -> Font {
        let name: String
        switch weight {
        case .medium, .regular, .light: name = "Manrope-Medium"
        case .semibold: name = "Manrope-SemiBold"
        case .heavy, .black: name = "Manrope-ExtraBold"
        default: name = "Manrope-Bold"
        }
        return font(name, size)
    }

    private static func font(_ name: String, _ size: CGFloat) -> Font {
        Theme.followsAccessibilitySettings ? .custom(name, size: size, relativeTo: textStyle(for: size)) : .custom(name, size: size)
    }

    /// The system text style a design size scales with (the one whose default size is nearest).
    static func textStyle(for size: CGFloat) -> Font.TextStyle {
        switch size {
        case 31...: return .largeTitle          // 34
        case 25..<31: return .title             // 28
        case 21..<25: return .title2            // 22
        case 18.5..<21: return .title3          // 20
        case 16.5..<18.5: return .body          // 17
        case 15.5..<16.5: return .callout       // 16
        case 14..<15.5: return .subheadline     // 15
        case 12.5..<14: return .footnote        // 13
        case 11.5..<12.5: return .caption       // 12
        default: return .caption2               // 11
        }
    }
}

extension View {
    /// Caps Larger Text where the space is fixed (placed grid cells, the camera bar): text still
    /// grows, up to `limit`. Sheets opened from inside undo it with `fullDynamicType()`.
    func cappedDynamicType(_ limit: DynamicTypeSize = .accessibility1) -> some View {
        modifier(CappedDynamicType(limit: limit))
    }

    /// The person's own text size again (for a sheet or screen opened from a capped view).
    func fullDynamicType() -> some View {
        modifier(FullDynamicType())
    }
}

private struct FullDynamicTypeKey: EnvironmentKey {
    static let defaultValue: DynamicTypeSize? = nil
}

extension EnvironmentValues {
    /// The text size before a `cappedDynamicType()` above; nil when nothing above caps it.
    var fullDynamicTypeSize: DynamicTypeSize? {
        get { self[FullDynamicTypeKey.self] }
        set { self[FullDynamicTypeKey.self] = newValue }
    }
}

private struct CappedDynamicType: ViewModifier {
    var limit: DynamicTypeSize
    @Environment(\.dynamicTypeSize) private var size
    @Environment(\.fullDynamicTypeSize) private var outer

    func body(content: Content) -> some View {
        content
            .dynamicTypeSize(...limit)
            .environment(\.fullDynamicTypeSize, outer ?? size)
    }
}

private struct FullDynamicType: ViewModifier {
    @Environment(\.fullDynamicTypeSize) private var full

    @ViewBuilder func body(content: Content) -> some View {
        if let full {
            content
                .environment(\.dynamicTypeSize, full)
                .environment(\.fullDynamicTypeSize, nil)
        } else {
            content
        }
    }
}

/// The page background: navy with a blue glow top-right and a teal one bottom-left.
struct NightGlassBackground: View {
    var teal = true

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Theme.background
                RadialGradient(colors: [Theme.glowBlue, Theme.glowBlue.opacity(0)], center: UnitPoint(x: 1, y: -0.05),
                               startRadius: 0, endRadius: max(geo.size.width, 360) * 1.1)
                if teal {
                    RadialGradient(colors: [Theme.glowTeal, Theme.glowTeal.opacity(0)], center: UnitPoint(x: -0.1, y: 1.05),
                                   startRadius: 0, endRadius: max(geo.size.width, 360) * 0.9)
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// A glass card: translucent white over the background with a hairline border.
struct GlassCard: ViewModifier {
    var radius: CGFloat = Theme.cardRadius
    var padding: CGFloat = 12
    var fill: Color = Theme.card
    var border: Color = Theme.cardBorder
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        let strong = StrongGlass.on(contrast, reduceTransparency)
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        return content
            .padding(padding)
            // Fills its grid row's height so side-by-side cards line up.
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background {
                ZStack {
                    if strong { shape.fill(Theme.strongCardFill) }
                    if !(strong && fill == Theme.card) { shape.fill(fill) }
                }
            }
            .overlay(shape.stroke(strong && border == Theme.cardBorder ? Theme.strongBorder : border, lineWidth: strong ? 1.5 : 1))
    }
}

extension View {
    func glassCard(radius: CGFloat = Theme.cardRadius, padding: CGFloat = 12, fill: Color = Theme.card,
                   border: Color = Theme.cardBorder) -> some View {
        modifier(GlassCard(radius: radius, padding: padding, fill: fill, border: border))
    }
}

/// A rounded pill button like the boards' chips ("Lock", "All off", camera names).
struct PillButtonStyle: ButtonStyle {
    var selected = false
    var height: CGFloat = 44
    var prominent = false

    func makeBody(configuration: Configuration) -> some View {
        PillButtonFace(label: configuration.label, pressed: configuration.isPressed, selected: selected, height: height,
                       prominent: prominent)
    }
}

private struct PillButtonFace<Label: View>: View {
    let label: Label
    let pressed: Bool
    let selected: Bool
    let height: CGFloat
    let prominent: Bool
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        let filled = selected || prominent
        let strong = StrongGlass.on(contrast, reduceTransparency)
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        label
            .font(AppFont.manrope(14, .heavy))
            .padding(.horizontal, 14)
            .frame(minHeight: height)
            .foregroundColor(filled ? Theme.background : Theme.text)
            .background(shape.fill(filled ? Theme.text : (strong ? Theme.strongCardFill : Theme.control)))
            .overlay(shape.stroke(filled ? Color.clear : (strong ? Theme.strongBorder : Theme.controlBorder), lineWidth: strong ? 1.5 : 1))
            .opacity(pressed ? 0.7 : 1)
            // A chosen chip (camera, filter, mode) says so, not only by its fill.
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// Small capsule chip ("All secure", "17° Clear").
struct StatusChip: View {
    var text: String
    var symbol: String?
    var tint: Color?
    var dot: Color?
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        let strong = StrongGlass.on(contrast, reduceTransparency)
        HStack(spacing: 6) {
            if let dot { Circle().fill(dot).frame(width: 7, height: 7) }
            if let symbol { Image(systemName: symbol).font(.system(size: 13, weight: .bold)).foregroundColor(tint ?? Theme.text) }
            Text(text).font(AppFont.manrope(13, .bold)).lineLimit(1)
        }
        .foregroundColor(Theme.text)
        .padding(.horizontal, 12)
        .frame(minHeight: 32)
        .background(Capsule().fill(strong ? Theme.strongCardFill : (tint ?? Color.white).opacity(tint == nil ? 0.07 : 0.16)))
        .overlay(Capsule().stroke(strong ? (tint ?? Theme.strongBorder) : (tint ?? Color.white).opacity(tint == nil ? 0.10 : 0.35),
                                  lineWidth: strong ? 1.5 : 1))
        // One element: the text says it all (the icon and dot only repeat it).
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}
