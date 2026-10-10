package ca.nimaste.newman.core.voice

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * GET /api/voice/wakeword: where the wake-word model files are on the hub and how keenly to
 * listen (the tablet's sensitivity is already folded into [threshold]). A new [version] (a
 * retrained model) is downloaded again; the same one is kept on the tablet.
 */
@Serializable
data class WakeConfig(
    val name: String = "hey_newman",
    val phrase: String = "Hey Newman",
    val version: String = "",
    val threshold: Float = 0.5f,
    val files: Map<String, String> = emptyMap(),
    val enabled: Boolean = true,
) {
    val complete: Boolean get() = version.isNotBlank() && FILES.all { files[it]?.isNotBlank() == true }

    companion object {
        val FILES = listOf("melspectrogram", "embedding_model", "wakeword")
        private val json = Json { ignoreUnknownKeys = true }
        fun parse(text: String): WakeConfig = json.decodeFromString(serializer(), text)
    }
}

/** What the wall page tells the app (NewmanWall.voiceConfig): listen or not, and the threshold. */
@Serializable
data class VoicePageConfig(val enabled: Boolean = false, val threshold: Float = 0.5f, val version: String = "") {
    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        fun parse(text: String?): VoicePageConfig =
            try {
                if (text.isNullOrBlank()) VoicePageConfig() else json.decodeFromString(serializer(), text)
            } catch (e: Exception) {
                VoicePageConfig()
            }
    }
}
