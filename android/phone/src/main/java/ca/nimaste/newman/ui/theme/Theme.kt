package ca.nimaste.newman.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.R

/** The "night glass" look of the design boards (PhoneOverview & co). */
object N {
    val Night = Color(0xFF0B1026)
    val Black = Color(0xFF05070F)
    val Text = Color(0xFFEEF2FF)
    val Muted = Color(0xFFA9B4D6)
    val Soft = Color(0xFFC9D2EE)
    val TabOff = Color(0xFF8E9AC0)
    val Glass = Color(0x12FFFFFF)       // rgba(255,255,255,0.07)
    val GlassHi = Color(0x1FFFFFFF)     // 0.12
    val Border = Color(0x1AFFFFFF)      // 0.10
    val BorderHi = Color(0x29FFFFFF)    // 0.16
    val Divider = Color(0x14FFFFFF)     // 0.08
    val Teal = Color(0xFF3CCFB4)
    val TealBg = Color(0x293CCFB4)      // 0.16
    val TealBorder = Color(0x593CCFB4)  // 0.35
    val Blue = Color(0xFF7FB8FF)
    val Cool = Color(0xFF5AA9FF)
    val Heat = Color(0xFFFF8A5B)
    val Amber = Color(0xFFF5B942)
    val AmberText = Color(0xFF241A05)
    val Coral = Color(0xFFFF8A6B)
    val CoralBg = Color(0x2EFF7A59)     // 0.18
    val CoralCard = Color(0x1AFF7A59)   // 0.10
    val CoralBorder = Color(0x59FF7A59) // 0.35
    val CoralText = Color(0xFFFFB29E)
    val Red = Color(0xFFFF5C5C)
    val Live = Color(0xFFE5484D)
    val Neutral = Color(0xFFC9D4F5)
    val GlowBlue = Color(0xFF1E3A8A)
    val GlowTeal = Color(0xFF0F5E57)
    val Bar = Color(0xDB0B1026)         // tab bar rgba(11,16,38,0.86)
}

/** The phone's Home, Cameras, Devices and More (the approved phone mockups): solid surfaces and tones. */
object H {
    val Page = Color(0xFF0A1024)
    val Card = Color(0xFF131B36)
    val CardLine = Color(0xFF222C55)
    val Raised = Color(0xFF18214A)
    val RaisedLine = Color(0xFF2B3766)
    val TileLine = Color(0xFF263160)
    val Inset = Color(0xFF0E1530)
    val Picture = Color(0xFF0C1330)
    val Selected = Color(0xFF2A3570)
    val Track = Color(0xFF26305C)
    val SwitchOff = Color(0xFF2A3466)
    val Knob = Color(0xFFC9D2EF)
    val Accent = Color(0xFF7AA2FF)
    val Info = Color(0xFF9DB8FF)
    val Text = Color(0xFFEEF2FF)
    val Soft = Color(0xFFC9D2EF)
    val Sub = Color(0xFFA3AED0)
    val Label = Color(0xFF8F9BC4)
    val Placeholder = Color(0xFF6F7BA6)
    // all good (teal)
    val Good = Color(0xFF4FD1A5)
    val GoodText = Color(0xFF7CE3C0)
    val GoodBg = Color(0xFF0F2A26)
    val GoodLine = Color(0xFF1D5A4D)
    val GoodHead = Color(0xFFB8F0DC)
    val GoodSub = Color(0xFF8FD8BF)
    // lights on (yellow)
    val Light = Color(0xFFF5C451)
    val LightText = Color(0xFFF5D27A)
    val LightBg = Color(0xFF2A2414)
    val LightLine = Color(0xFF5A4A1F)
    val LightTrack = Color(0xFF3D3418)
    // warm (climate) and open / not secure
    val Warm = Color(0xFFFF9A4D)
    val WarmText = Color(0xFFFFB27A)
    val WarmBg = Color(0xFF3A2414)
    val Hot = Color(0xFF2A1712)
    val HotLine = Color(0xFF5C2B1F)
    val HotText = Color(0xFFFFB39C)
    // danger banner (red)
    val DangerBg = Color(0xFF3A1420)
    val DangerLine = Color(0xFF7A2A3A)
    val DangerBubble = Color(0xFF5C1F2E)
    val DangerIcon = Color(0xFFFF8F9A)
    val DangerHead = Color(0xFFFFD0D6)
    val DangerSub = Color(0xFFFFB3BD)
    val DangerButton = Color(0xFFFF6B7A)
    val DangerButtonText = Color(0xFF2A0A10)
    val RemoveBg = Color(0xFF3A1A22)
    // a warning (amber)
    val WarnBg = Color(0xFF2A1D10)
    val WarnLine = Color(0xFF5A3F1A)
    val WarnText = Color(0xFFF7CF8F)
    val WarnSub = Color(0xFFC9A46A)
    val People = Color(0xFFC3A8FF)
    val PeopleBg = Color(0xFF2A2152)
    val Badge = Color(0xFFE5484D)
    val LiveDot = Color(0xFFFF5D5D)
}

val Sora = FontFamily(
    Font(R.font.sora, FontWeight.Medium),
    Font(R.font.sora, FontWeight.SemiBold),
)

val Manrope = FontFamily(
    Font(R.font.manrope, FontWeight.Medium),
    Font(R.font.manrope, FontWeight.SemiBold),
    Font(R.font.manrope, FontWeight.Bold),
    Font(R.font.manrope, FontWeight.ExtraBold),
)

object T {
    val display = TextStyle(fontFamily = Sora, fontWeight = FontWeight.SemiBold, fontSize = 28.sp, color = N.Text)
    val title = TextStyle(fontFamily = Sora, fontWeight = FontWeight.SemiBold, fontSize = 22.sp, color = N.Text)
    val number = TextStyle(fontFamily = Sora, fontWeight = FontWeight.SemiBold, fontSize = 30.sp, color = N.Text)
    val label = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 15.sp, color = N.Text)
    val body = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, color = N.Text)
    val small = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.Bold, fontSize = 13.sp, color = N.Text)
    val tiny = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.Bold, fontSize = 12.sp, color = N.Muted)
    val caps = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 12.sp, color = N.Muted, letterSpacing = 1.sp)
}

@Composable
fun NewmanTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = N.Text, onPrimary = N.Night, secondary = N.Teal, background = N.Night, surface = Color(0xFF151B36),
            onSurface = N.Text, onBackground = N.Text, surfaceVariant = Color(0xFF1E2546), onSurfaceVariant = N.Muted,
            error = N.Coral, outline = N.BorderHi,
        ),
        typography = Typography(
            bodyLarge = T.body.copy(fontSize = 16.sp), bodyMedium = T.body, bodySmall = T.tiny,
            titleLarge = T.title, titleMedium = T.label, labelLarge = T.label, labelMedium = T.small,
        ),
        content = content,
    )
}

/** The page background: night blue with a soft blue glow top-right and a teal one bottom-left. */
@Composable
fun NightBackground(modifier: Modifier = Modifier, teal: Boolean = true, content: @Composable BoxScope.() -> Unit) {
    val d = LocalDensity.current.density
    Box(
        modifier
            .background(N.Night)
            .drawBehind {
                drawRect(Brush.radialGradient(listOf(N.GlowBlue, N.GlowBlue.copy(alpha = 0f)),
                    center = Offset(size.width, -0.05f * size.height), radius = 480f * d))
                if (teal) drawRect(Brush.radialGradient(listOf(N.GlowTeal, N.GlowTeal.copy(alpha = 0f)),
                    center = Offset(-0.1f * size.width, 1.05f * size.height), radius = 400f * d))
            },
        content = content,
    )
}
