package ca.nimaste.newman.core.model

/** The control a command's `valueType` asks for (kinds.py Command.value_type). */
enum class ControlType {
    /** A one-tap button (no value). */
    BUTTON,
    /** 0-100 slider ("percent"). */
    SLIDER,
    /** −/+ stepper with min/max/step/unit ("number"). */
    STEPPER,
    /** One of the kind's states ("state"), e.g. a thermostat mode. */
    STATE_PICKER,
    /** Pick any of the device's attrs[attr] items ("choices"), e.g. a vacuum's rooms. */
    CHOICE_CHIPS,
    /** Free text ("text"), e.g. a webhook's message. */
    TEXT,
}

object Controls {
    fun controlFor(cmd: Command): ControlType = when (cmd.valueType) {
        "percent" -> ControlType.SLIDER
        "number" -> ControlType.STEPPER
        "state" -> ControlType.STATE_PICKER
        "choices" -> ControlType.CHOICE_CHIPS
        "text" -> ControlType.TEXT
        // An unknown value type that still takes a value: a text field is the safe fallback.
        "" -> if (cmd.takesValue) ControlType.TEXT else ControlType.BUTTON
        else -> if (cmd.takesValue) ControlType.TEXT else ControlType.BUTTON
    }

    /** One row of the device sheet. Plain commands may collapse into a switch (on/off pair). */
    sealed interface Row {
        data class Switch(val pair: HubModel.Duo) : Row
        data class Value(val command: Command, val control: ControlType) : Row
        data class Buttons(val commands: List<Command>) : Row
    }

    /**
     * Every command of a kind, laid out for the device sheet: an on/off pair becomes a switch, value
     * commands get the control their valueType asks for, the rest are buttons. A device that
     * says it isn't dimmable (attrs.dimmable == false) gets no percent slider.
     */
    fun rowsFor(e: Entity, k: Kind?): List<Row> {
        if (k == null) return emptyList()
        val plain = HubModel.plainCommands(k)
        val pair = HubModel.switchPair(k, plain)
        val rows = mutableListOf<Row>()
        if (pair != null) rows += Row.Switch(pair)
        k.commands.filter { it.takesValue || it.valueType.isNotEmpty() }.forEach { c ->
            val type = controlFor(c)
            if (type == ControlType.BUTTON) return@forEach
            if (type == ControlType.SLIDER && e.attrs.bool("dimmable") == false) return@forEach
            if (type == ControlType.STATE_PICKER && HubModel.stateChoices(k).isEmpty()) return@forEach
            rows += Row.Value(c, type)
        }
        val buttons = plain.filter { pair == null || (it != pair.on && it != pair.off) }
        if (buttons.isNotEmpty()) rows += Row.Buttons(buttons)
        return rows
    }
}

/** Sensitive commands (unlock, disarm, open the garage, sound a siren) need the phone's owner. */
object Sensitive {
    /** True when [commandId] on [kind] must pass BiometricPrompt before it is sent. */
    fun needsConfirmation(kind: Kind?, commandId: String, biometricsEnabled: Boolean): Boolean {
        if (!biometricsEnabled) return false
        return kind?.command(commandId)?.sensitive == true
    }

    /** A notification button ("unlock:<entity>", "open_all", "snooze:60"…) the hub marked sensitive. */
    fun needsConfirmation(button: PushButton, biometricsEnabled: Boolean): Boolean =
        biometricsEnabled && button.sensitive
}
