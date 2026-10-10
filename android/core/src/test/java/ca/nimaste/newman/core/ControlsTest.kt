package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.ControlType
import ca.nimaste.newman.core.model.Controls
import ca.nimaste.newman.core.model.Controls.Row
import ca.nimaste.newman.core.model.PushButton
import ca.nimaste.newman.core.model.Sensitive
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ControlsTest {
    private val meta = Fixtures.meta

    @Test
    fun valueTypePicksTheControl() {
        assertEquals(ControlType.SLIDER, Controls.controlFor(Command("a", valueType = "percent", takesValue = true)))
        assertEquals(ControlType.STEPPER, Controls.controlFor(Command("a", valueType = "number", takesValue = true)))
        assertEquals(ControlType.STATE_PICKER, Controls.controlFor(Command("a", valueType = "state", takesValue = true)))
        assertEquals(ControlType.CHOICE_CHIPS, Controls.controlFor(Command("a", valueType = "choices", takesValue = true)))
        assertEquals(ControlType.TEXT, Controls.controlFor(Command("a", valueType = "text", takesValue = true)))
        assertEquals(ControlType.BUTTON, Controls.controlFor(Command("a")))
        // A value type this app doesn't know yet still gets an input rather than a dead button.
        assertEquals(ControlType.TEXT, Controls.controlFor(Command("a", valueType = "colour", takesValue = true)))
    }

    @Test
    fun everyRealCommandGetsAControl() {
        meta.kinds.flatMap { it.commands }.forEach { c ->
            val type = Controls.controlFor(c)
            if (c.takesValue) assertTrue("${c.id} takes a value", type != ControlType.BUTTON) else assertEquals(ControlType.BUTTON, type)
        }
    }

    @Test
    fun thermostatGetsModePickerAndStepper() {
        val e = Fixtures.entity("thermostat")
        val rows = Controls.rowsFor(e, meta.kind(e.kind))
        val values = rows.filterIsInstance<Row.Value>().map { it.control }
        assertTrue(ControlType.STATE_PICKER in values)
        assertTrue(ControlType.STEPPER in values)
    }

    @Test
    fun lightGetsSwitchAndSliderUnlessNotDimmable() {
        val e = Fixtures.entity("light")
        val k = meta.kind(e.kind)
        val rows = Controls.rowsFor(e, k)
        assertTrue(rows.first() is Row.Switch)
        assertTrue(rows.any { it is Row.Value && it.control == ControlType.SLIDER })
        val plain = e.copy(attrs = JsonObject(e.attrs + ("dimmable" to JsonPrimitive(false))))
        assertFalse(Controls.rowsFor(plain, k).any { it is Row.Value && it.control == ControlType.SLIDER })
    }

    @Test
    fun lockWithASensitiveCommandIsButtonsNotASwitch() {
        val e = Fixtures.entity("lock")
        val rows = Controls.rowsFor(e, meta.kind(e.kind))
        assertFalse(rows.any { it is Row.Switch })
        val buttons = rows.filterIsInstance<Row.Buttons>().single().commands.map { it.id }
        assertEquals(listOf("lock", "unlock"), buttons)
    }

    @Test
    fun vacuumGetsRoomChipsAndButtons() {
        val e = Fixtures.entity("vacuum")
        val rows = Controls.rowsFor(e, meta.kind(e.kind))
        assertTrue(rows.any { it is Row.Value && it.control == ControlType.CHOICE_CHIPS })
        assertTrue(rows.filterIsInstance<Row.Buttons>().single().commands.size >= 3)
    }

    @Test
    fun sensitiveCommandsNeedConfirmationOnlyWhenTheSwitchIsOn() {
        val lock = meta.kind("lock")
        assertTrue(Sensitive.needsConfirmation(lock, "unlock", biometricsEnabled = true))
        assertFalse(Sensitive.needsConfirmation(lock, "lock", biometricsEnabled = true))
        assertFalse(Sensitive.needsConfirmation(lock, "unlock", biometricsEnabled = false))
        assertFalse(Sensitive.needsConfirmation(null, "unlock", biometricsEnabled = true))
        // The hub marks the sensitive ones; no command id is special to the app.
        val sensitive = meta.kinds.flatMap { k -> k.commands.filter { it.sensitive }.map { "${k.id}.${it.id}" } }
        assertTrue(sensitive.containsAll(listOf("lock.unlock", "alarm.disarm", "garage.open", "siren.turn_on")))
        sensitive.forEach { id ->
            val (kind, cmd) = id.split('.')
            assertTrue(Sensitive.needsConfirmation(meta.kind(kind), cmd, true))
        }
        assertTrue(Sensitive.needsConfirmation(PushButton("unlock:x", sensitive = true), true))
        assertFalse(Sensitive.needsConfirmation(PushButton("lock:x", sensitive = false), true))
    }
}
