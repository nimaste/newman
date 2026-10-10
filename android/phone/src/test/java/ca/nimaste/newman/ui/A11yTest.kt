package ca.nimaste.newman.ui

import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.CompositionLocalProvider
import ca.nimaste.newman.core.model.TextScale
import ca.nimaste.newman.ui.common.CappedFontScale
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.IconAction
import ca.nimaste.newman.ui.common.NSwitch
import ca.nimaste.newman.ui.common.Pill
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.common.tapAndHold
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Spot checks of what TalkBack gets from the app's building blocks (no hub needed). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = Application::class)
class A11yTest {
    @get:Rule val rule = createComposeRule()

    private fun hasRole(role: Role) = SemanticsMatcher.expectValue(SemanticsProperties.Role, role)

    /** The area that answers a touch (what Android's accessibility checks measure) is at least 48dp each way. */
    private fun SemanticsNodeInteraction.assertTouch48(): SemanticsNodeInteraction {
        val node = fetchSemanticsNode()
        val px = with(rule.density) { 48.dp.toPx() } - 0.5f
        val b = node.touchBoundsInRoot
        assertTrue("touch area ${b.width}x${b.height}px, want 48dp ($px px)", b.width >= px && b.height >= px)
        return this
    }

    @Test
    fun iconOnlyButtonsHaveALabelAndA48dpTarget() {
        rule.setContent { IconAction("chevronRight", "Next camera", size = 40.dp) {} }
        rule.onNodeWithContentDescription("Next camera")
            .assertHasClickAction()
            .assert(hasRole(Role.Button))
            .assertTouch48()
    }

    @Test
    fun smallButtonsStillTouchAt48dp() {
        rule.setContent { GlassButton("Remove", height = 38.dp) {} }
        rule.onNodeWithText("Remove").assertHasClickAction().assertTouch48()
    }

    @Test
    fun switchesSayWhatTheySwitchAndTheirState() {
        rule.setContent {
            var on by remember { mutableStateOf(false) }
            NSwitch(on, label = "Confirm before unlocking") { on = it }
        }
        val node = rule.onNodeWithContentDescription("Confirm before unlocking")
        node.assert(hasRole(Role.Switch)).assertIsOff()
        node.performClick()
        node.assertIsOn()
    }

    @Test
    fun deviceTilesOfferTheirTapAndHoldActionsByName() {
        rule.setContent {
            Text("Den plug", Modifier.tapAndHold(onTap = {}, onHold = {}, tapLabel = "turn off", holdLabel = "show all controls"))
        }
        val config = rule.onNodeWithText("Den plug").fetchSemanticsNode().config
        assertEquals("turn off", config.getOrNull(androidx.compose.ui.semantics.SemanticsActions.OnClick)?.label)
        assertEquals("show all controls", config.getOrNull(androidx.compose.ui.semantics.SemanticsActions.OnLongClick)?.label)
    }

    @Test
    fun filtersAreSelectableTabsAndTitlesAreHeadings() {
        rule.setContent {
            Column {
                Text("Alerts", Modifier.heading())
                Pill("Cameras", selected = true) {}
            }
        }
        rule.onNodeWithText("Alerts").assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Heading))
        rule.onNodeWithText("Cameras").assert(hasRole(Role.Tab)).assertIsSelected().assertTouch48()
    }

    @Test
    fun cellsCapTheFontScale() {
        var inside = 0f
        rule.setContent {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, 2f)) {
                CappedFontScale { inside = LocalDensity.current.fontScale }
            }
        }
        rule.waitForIdle()
        assertEquals(TextScale.CELL_MAX, inside, 0f)
    }
}
