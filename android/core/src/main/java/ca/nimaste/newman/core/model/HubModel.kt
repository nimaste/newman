package ca.nimaste.newman.core.model

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.text.NumberFormat
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/**
 * Reading devices generically through /api/meta - a Kotlin port of web/src/model.js (and the
 * wall's tile helpers). No function here knows a kind of device by its id.
 */
object HubModel {

    fun isActive(e: Entity?, k: Kind?): Boolean =
        k?.activeState != null && e != null && e.state == k.activeState

    fun stateLabelOf(k: Kind?, stateId: String?): String? {
        if (stateId == null) return null
        return k?.states?.firstOrNull { it.id == stateId }?.label ?: stateId
    }

    /** The device's state in words: the hub's own stateLabel when it gives one ("Screen off"), else the kind's label for the state. */
    fun stateLabel(e: Entity?, k: Kind?): String? =
        e?.stateLabel?.trim()?.takeIf { it.isNotEmpty() } ?: stateLabelOf(k, e?.state)

    fun activeLabel(k: Kind?): String = if (k?.activeState != null) stateLabelOf(k, k.activeState) ?: "" else ""

    fun isOffline(e: Entity?): Boolean = e == null || !e.available || e.state == "offline"

    fun isTransitional(e: Entity?, k: Kind?): Boolean = k != null && e != null && e.state in k.transitional

    /** Worth a look: settled away from the kind's secure state (unlocked, open). */
    fun notSecure(e: Entity?, k: Kind?): Boolean =
        k?.secureState != null && e != null && e.available && !e.state.isNullOrEmpty() &&
            e.state != k.secureState && !isTransitional(e, k)

    fun isMode(k: Kind?): Boolean = k?.isMode == true

    /** Events a device can actually emit: the kind's, narrowed to attrs.events when present. */
    fun eventsFor(e: Entity?, k: Kind?): List<KindEvent> {
        val all = k?.events ?: emptyList()
        val own = e?.attrs?.array("events")?.mapNotNull { it.asStringOrNull } ?: return all
        val unknown = own.filter { id -> all.none { it.id == id } }
            .map { KindEvent(it, it.replaceFirstChar { c -> c.uppercase() }.replace('_', ' ')) }
        return all.filter { it.id in own } + unknown
    }

    fun isRemote(e: Entity?, k: Kind?): Boolean =
        e?.attrs?.array("events") != null && (k?.commands ?: emptyList()).isEmpty() && (k?.states ?: emptyList()).size <= 1

    private val PLURAL = mapOf("person" to "people", "bus" to "buses", "license_plate" to "license plates",
        "face" to "faces", "package" to "packages")

    fun countLabel(label: String, n: Int): String {
        val name = label.replace('_', ' ')
        return "$n ${if (n == 1) name else PLURAL[label] ?: "${name}s"}"
    }

    fun countList(counts: JsonObject?): List<String> =
        counts?.entries?.mapNotNull { (k, v) ->
            val n = v.asDoubleOrNull ?: return@mapNotNull null
            if (n > 0) countLabel(k, n.toInt()) else null
        } ?: emptyList()

    fun shortEventLabel(label: String): String {
        Regex("^[“\"](.+)[”\"]\\s+is\\s.*pressed$", RegexOption.IGNORE_CASE).find(label)?.let { return it.groupValues[1] }
        return label.replace(Regex("\\s+(pressed|detected|entered|comes in)$", RegexOption.IGNORE_CASE), "").ifEmpty { label }
    }

    private fun fmtTemp(v: Double?): String? = v?.let { String.format(Locale.US, "%.1f", (it * 10).roundToLong() / 10.0) }

    /** Every state in plain words (model.js entityStateText). */
    fun entityStateText(e: Entity?, k: Kind?, long: Boolean = false): String {
        if (e == null) return ""
        if (isOffline(e)) return "Offline"
        val a = e.attrs
        val label = e.stateLabel.ifEmpty { e.state ?: "—" }

        if (isRemote(e, k)) {
            val names = eventsFor(e, k).map { shortEventLabel(it.label) }.distinct()  // a long press is the same button
            val word = if (eventsFor(e, k).all { it.label.contains("pressed", ignoreCase = true) }) "button" else "event"
            if (names.isEmpty()) return "No ${word}s"
            if (long || names.size <= 2) return word.replaceFirstChar { it.uppercase() } + "s: " + names.joinToString(", ")
            return "${names.size} ${word}s (${names.take(2).joinToString(", ")}, …)"
        }
        a.obj("counts")?.let { countsObj ->
            val counts = countList(countsObj)
            val act = activeLabel(k)
            if (counts.isEmpty()) return if (act.isNotEmpty()) label else "Clear"
            return (if (act.isNotEmpty()) listOf(act) + counts else counts).joinToString(" · ")
        }
        a.number("currentTemp")?.let { cur ->
            val unit = a.string("unit") ?: k?.unit?.ifEmpty { null } ?: "°C"
            val now = "${fmtTemp(cur)} $unit"
            val target = fmtTemp(a.number("targetTemp"))
            return if (e.state == "off" || target == null) "$label · $now" else "$label · $now → $target"
        }
        val reading = readingText(e, k)
        if (reading.isNotEmpty()) return reading
        if (k != null && k.states.isEmpty() && k.commands.isNotEmpty() && e.state.isNullOrEmpty()) return ""
        if (isTransitional(e, k)) return label.removeSuffix("…") + "…"
        if (k != null && k.events.any { it.id == e.state }) return "$label just now"
        a.string("track")?.takeIf { it.isNotEmpty() }?.let { track ->
            val artist = a.string("artist")?.takeIf { it.isNotEmpty() }
            return "$label · $track" + (artist?.let { " — $it" } ?: "")
        }
        val b = a.number("brightness")
        val on = if (k?.activeState != null) e.state == k.activeState else e.state != "off"
        if (b != null && on && a.bool("dimmable") != false) return "$label · ${b.roundToInt()}%"
        return label
    }

    /** For kinds without states whose state is a number: "Humidity · 64 %". */
    fun readingText(e: Entity?, k: Kind?, withLabel: Boolean = true): String {
        if (e == null || k == null || k.states.isNotEmpty()) return ""
        val v = e.state?.trim()?.takeIf { it.isNotEmpty() }?.toDoubleOrNull() ?: return ""
        val a = e.attrs
        val prop = e.properties.firstOrNull { it.attr.isEmpty() }
        val unit = a.string("unitOfMeasurement")?.ifEmpty { null } ?: prop?.unit?.ifEmpty { null } ?: k.unit
        val num = if (abs(v) >= 100) v.roundToLong().toDouble() else (v * 10).roundToLong() / 10.0
        val text = NumberFormat.getNumberInstance(Locale.getDefault()).format(num) + if (unit.isNotEmpty()) " $unit" else ""
        val cls = a.string("deviceClass")?.replace('_', ' ') ?: ""
        return if (withLabel && cls.isNotEmpty()) cls.replaceFirstChar { it.uppercase() } + " · " + text else text
    }

    /** [area, devices] sorted by room; devices without a room last. */
    fun groupByArea(list: List<Entity>): List<Pair<String?, List<Entity>>> {
        if (list.none { !it.attrs.string("area").isNullOrEmpty() }) return listOf(null to list)
        val key = { e: Entity -> e.attrs.string("area") ?: "" }
        val areas = list.map(key).distinct().sortedWith { a, b ->
            when {
                a.isEmpty() && b.isNotEmpty() -> 1
                b.isEmpty() && a.isNotEmpty() -> -1
                else -> a.compareTo(b, ignoreCase = true)
            }
        }
        return areas.map { area -> (area.ifEmpty { "No room" }) to list.filter { key(it) == area } }
    }

    /** Commands usable as one-tap actions (no value needed). */
    fun plainCommands(k: Kind?): List<Command> = k?.commands?.filter { !it.takesValue } ?: emptyList()

    fun choiceCommands(k: Kind?): List<Command> = k?.commands?.filter { it.valueType == "choices" } ?: emptyList()

    data class Duo(val on: Command, val off: Command)

    /** A start/stop duo among several buttons (only the one that makes sense now is shown). */
    fun toggleDuo(k: Kind?, plain: List<Command>): Duo? {
        if (k?.activeState == null || plain.size <= 2) return null
        if (k.commands.any { it.valueType == "percent" }) return null
        val on = plain.firstOrNull { it.confirmsTo == k.activeState } ?: return null
        val off = plain.firstOrNull { it !== on && it.confirmsTo.isNotEmpty() && it.confirmsTo != k.activeState } ?: return null
        return Duo(on, off)
    }

    /** The on/off pair shown as a switch: exactly two plain, non-sensitive commands. */
    fun switchPair(k: Kind?, plain: List<Command> = plainCommands(k)): Duo? {
        if (k?.activeState == null || plain.size != 2 || plain.any { it.sensitive }) return null
        val on = plain.firstOrNull { it.confirmsTo == k.activeState } ?: return null
        val off = plain.firstOrNull { it !== on && it.confirmsTo.isNotEmpty() && it.confirmsTo != k.activeState } ?: return null
        return Duo(on, off)
    }

    /** The states a "state" command can set: the kind's states without the momentary ones. */
    fun stateChoices(k: Kind?): List<KindState> = k?.states?.filter { it.id !in k.transitional } ?: emptyList()

    /** A value command's current value: attrs[cmd.attr], or the state for a "state" command. */
    fun commandCurrent(e: Entity?, cmd: Command?): JsonElement? {
        if (e == null || cmd == null) return null
        if (cmd.attr.isNotEmpty()) return e.attrs[cmd.attr]
        if (cmd.valueType == "state") return e.state?.let { JsonPrimitive(it) }
        return null
    }

    data class ChoiceItem(val id: JsonElement, val name: String)

    /** What a "choices" command can pick from: attrs[cmd.attr] = [{id, name}]. */
    fun choiceItems(e: Entity?, cmd: Command?): List<ChoiceItem> {
        if (e == null || cmd == null || cmd.attr.isEmpty()) return emptyList()
        val list = e.attrs.array(cmd.attr) ?: return emptyList()
        return list.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            val id = o["id"] ?: return@mapNotNull null
            if (id is kotlinx.serialization.json.JsonNull) return@mapNotNull null
            ChoiceItem(id, o.string("name")?.ifEmpty { null } ?: id.asStringOrNull ?: id.toString())
        }
    }

    /** "80%", "21.5 °C", "Heat", "Kitchen and Hall" for a command value. */
    fun valueText(cmd: Command?, k: Kind?, v: JsonElement?, e: Entity? = null): String {
        if (cmd?.valueType == "choices") {
            val ids = (v.asArrayOrNull?.toList() ?: listOfNotNull(v?.takeIf { it.asStringOrNull?.isNotEmpty() == true }))
            if (ids.isEmpty()) return "nothing chosen"
            val items = choiceItems(e, cmd)
            val names = ids.map { id -> items.firstOrNull { it.id.asStringOrNull == id.asStringOrNull }?.name ?: (id.asStringOrNull ?: id.toString()) }
            return if (names.size <= 3) names.joinToString(if (names.size == 2) " and " else ", ")
            else "${names.take(2).joinToString(", ")} and ${names.size - 2} more"
        }
        val s = v.asStringOrNull
        if (s.isNullOrEmpty()) return "?"
        if (cmd?.valueType == "state") return stateLabelOf(k, s) ?: s
        val shown = v.asNumberOrNull?.plain() ?: s
        val u = cmd?.unit ?: ""
        if (u.isEmpty()) return shown
        return if (u == "%") "$shown%" else "$shown $u"
    }

    fun issuesOf(e: Entity?): List<String> = e?.health?.issues ?: emptyList()

    /** Kind icon name: a nicety map, then capabilities, then category (model.js kindIcon). */
    fun kindIcon(k: Kind?): String {
        if (k == null) return "dot"
        val byKind = mapOf("lock" to "lock", "doorbell" to "bell", "contact" to "door", "motion" to "motion",
            "alarm" to "shield", "camera" to "camera", "light" to "light", "remote" to "grid", "zone" to "camera",
            "thermostat" to "thermometer", "temperature" to "thermometer", "garage" to "garage", "weather" to "cloud",
            "calendar" to "calendar", "vacuum" to "vacuum", "switch" to "power", "fan" to "fan", "cover" to "blinds",
            "sensor" to "gauge", "leak" to "droplet", "smoke" to "flame", "binary" to "toggle", "siren" to "megaphone",
            "button" to "pointer", "speaker" to "speaker", "webhook" to "webhook")
        val byCat = mapOf("security" to "shield", "garage" to "garage", "lighting" to "light",
            "climate" to "thermometer", "cleaning" to "vacuum", "cameras" to "camera")
        byKind[k.id]?.let { return it }
        if (k.hasCamera) return "camera"
        k.commands.firstOrNull { it.icon.isNotEmpty() }?.let { return it.icon }
        if (k.events.isNotEmpty()) return "bell"
        if (k.unit.isNotEmpty()) return "thermometer"
        return byCat[k.category] ?: "dot"
    }

    // -- the wall's tile helpers (tiles.jsx / DeviceSheet.jsx) ------------------------------------

    /** The action a list row offers: a command that isn't the current state (Lock/Unlock). */
    fun nextAction(e: Entity, k: Kind?): Command? {
        val plain = plainCommands(k)
        return plain.firstOrNull { it.confirmsTo.isNotEmpty() && it.confirmsTo != e.state }
            ?: if (plain.size == 1) plain[0] else null
    }

    /** Tap on a device card: its on/off pair, else the next action. */
    fun tapAction(e: Entity, k: Kind?): Command? {
        val plain = plainCommands(k)
        if (k?.activeState != null && plain.size == 2 && plain.none { it.sensitive }) {
            val on = plain.firstOrNull { it.confirmsTo == k.activeState }
            val off = plain.firstOrNull { it !== on && it.confirmsTo.isNotEmpty() }
            if (on != null && off != null) return if (isActive(e, k)) off else on
        }
        return nextAction(e, k)
    }

    /** Actions beyond the main tap: only for devices with more than an on/off pair. */
    fun extraActions(k: Kind?, main: Command?): List<Command> {
        val plain = plainCommands(k)
        val choices = choiceCommands(k)
        if (plain.size <= 2 && choices.isEmpty()) return emptyList()
        val duo = toggleDuo(k, plain)
        return plain.filter { it != main && (duo == null || (it != duo.on && it != duo.off)) } + choices
    }

    data class Vitals(val battery: Int?, val problem: String?)

    /** Battery % and a reported problem, from the device's properties (any kind). */
    fun vitals(e: Entity): Vitals {
        val battery = e.properties.firstOrNull { it.id == "battery" && it.value.asNumberOrNull != null }
        val problem = e.properties.firstOrNull { it.id == "problem" && !it.value.asStringOrNull.isNullOrEmpty() && it.value.asBooleanOrNull != false }
        return Vitals(battery?.value.asNumberOrNull?.roundToInt(), problem?.value.asStringOrNull)
    }

    /** "calm" (teal) in the secure state, "warn" (coral) away from it or with a problem, "" otherwise. */
    fun stateTone(e: Entity, k: Kind?, problem: String?): String {
        if (problem != null || notSecure(e, k)) return "warn"
        return if (k?.secureState != null) "calm" else ""
    }

    /** A dimmable percent control (a light's brightness), unless the device says it isn't dimmable. */
    fun percentCommand(e: Entity, k: Kind?): Command? {
        val c = k?.commands?.firstOrNull { it.valueType == "percent" } ?: return null
        return if (e.attrs.bool("dimmable") == false) null else c
    }

    /** formatPropValue (web/src/automation.js). */
    fun formatProp(p: Property?, v: JsonElement?): String {
        val s = v.asStringOrNull
        if (s.isNullOrEmpty()) return "—"
        if (p == null) return s
        if (p.type == "number") {
            val n = v.asDoubleOrNull
            val num = if (n != null && n.isFinite()) ((n * 100).roundToLong() / 100.0).plain() else s
            return if (p.unit.isNotEmpty()) (if (p.unit == "%") "$num%" else "$num ${p.unit}") else num
        }
        if (p.type == "boolean") return if (v.asBooleanOrNull == true || Regex("^(true|1|yes|on)$", RegexOption.IGNORE_CASE).matches(s)) "yes" else "no"
        p.options.firstOrNull { it.value.asStringOrNull == s }?.let { return it.label }
        return s.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }

    /** Devices worth a look right now: urgent kinds away from their secure state first, then sick ones. */
    data class Attention(val entity: Entity, val issues: List<String>, val alarm: Boolean)

    fun attention(entities: Collection<Entity>, meta: Meta?): List<Attention> {
        val alarms = entities.filter { val k = meta?.kind(it.kind); k != null && k.urgent && k.secureState != null && notSecure(it, k) }
        val sick = entities.filter { !it.health.ok && it !in alarms }
        return alarms.map { Attention(it, listOfNotNull(stateLabelOf(meta?.kind(it.kind), it.state)) + it.health.issues, true) } +
            sick.map { Attention(it, it.health.issues, false) }
    }

    /** "All secure" / "Side Door unlocked" / "2 not secure" (null when nothing has a secure state). */
    fun securitySummary(entities: Collection<Entity>, meta: Meta?): Pair<Boolean, String>? {
        val secure = entities.filter { meta?.kind(it.kind)?.secureState != null }
        if (secure.isEmpty()) return null
        val open = secure.filter { notSecure(it, meta?.kind(it.kind)) }
        return when {
            open.isEmpty() -> true to "All secure"
            open.size == 1 -> false to "${open[0].name} ${(open[0].stateLabel.ifEmpty { open[0].state ?: "" }).lowercase()}"
            else -> false to "${open.size} not secure"
        }
    }

    /** "1 light on" style count of active, non-security devices among [list]. */
    fun onCount(list: Collection<Entity>, meta: Meta?): Int =
        list.count { val k = meta?.kind(it.kind); isActive(it, k) && k?.secureState == null }

    /** Entities with this dashboard's "Show as" names applied. */
    fun withNames(entities: Map<String, Entity>, names: Map<String, String>): Map<String, Entity> {
        val clean = names.mapValues { it.value.trim().take(40) }.filterValues { it.isNotEmpty() }
        if (clean.isEmpty()) return entities
        return entities.mapValues { (id, e) -> clean[id]?.let { e.copy(name = it) } ?: e }
    }
}
