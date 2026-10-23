import Foundation

// Newman's alarms and timers ringing on the iPad wall: the settings, what the page asks to ring, the
// wall's own sounds, and the bridge's new calls (Shared/Core/WallRinging.swift, WallScreen.swift).

func runWallRingingTests() {
    eq(WallRinging.snoozeSetting(9), 9, "snooze: a choice")
    eq(WallRinging.snoozeSetting(7), 9, "snooze: not a choice -> 9")
    eq(WallRinging.ringSetting(30), 30, "ring: a choice")
    eq(WallRinging.ringSetting(0), 10, "ring: not a choice -> 10")
    eq(WallRinging.settingsJSON(snooze: 5, ring: 15), #"{"snoozeMinutes":5,"ringMinutes":15}"#, "settings for the page")

    let r = WallRinging.parseRing(#"{"kind":"timer","id":"tm_1","title":"pasta timer","ringMs":600000,"x":1}"#)
    check(r?.isTimer == true && r?.capSeconds == 600, "a timer ring, its cap")
    eq(WallRinging.parseRing(#"{"id":"a","ringMs":99999999}"#)?.capSeconds, 3600, "capped at an hour")
    check(WallRinging.parseRing(#"{"kind":"alarm"}"#) == nil && WallRinging.parseRing("nope") == nil, "no id / not JSON: nothing")

    eq(WallTones.names, ["Beeps", "Chimes", "Bell", "Rising"], "the four sounds")
    eq(WallTones.named("Cuckoo"), "Chimes", "an unknown sound -> Chimes")
    for name in WallTones.names {
        let pcm = WallTones.pcm(name)
        let peak = pcm.map { abs(Int($0)) }.max() ?? 0
        check(pcm.count > WallTones.rate && peak > 8000 && peak < 32768, "\(name): a loud loop of over a second")
        let wav = WallTones.wav(name)
        check(wav.count == 44 + pcm.count * 2 && wav.prefix(4) == Data("RIFF".utf8), "\(name): a WAV")
    }

    let js = WallBridgeJS.script(version: "1", dimmed: false, micAllowed: false, ringSettings: WallRinging.settingsJSON(snooze: 5, ring: 30))
    check(js.contains("var mic = false;") && js.contains(#"var ringing = "{\"snoozeMinutes\":5,\"ringMinutes\":30}";"#), "the bridge knows the mic and the settings")
    check(js.contains("ring: function") && js.contains("stopRing: function") && js.contains("openMicSettings: function"), "ring / stopRing / openMicSettings")
    check(js.contains("listen: listen === true"), "voiceDone(listen)")
    let st = WallBridgeJS.state(micAllowed: true, ringSettings: "{}")
    check(st.contains("_state(true,\"{}\")") && st.contains("newmanwall-resume"), "state: updated, and the page told")
    check(VoiceEvent.wakeFollowUp.contains("followUp") && VoiceEvent.cancelQuiet.contains("quiet"), "follow-up events")
}
