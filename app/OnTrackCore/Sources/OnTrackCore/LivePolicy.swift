import Foundation

/// Whether live transcription should run. Off means "record only": audio is saved and transcribed
/// with the accurate model when the session is saved.
public enum LivePolicy {
    public static func live(preferLive: Bool, onBattery: Bool, recordOnlyOnBattery: Bool) -> Bool {
        preferLive && !(onBattery && recordOnlyOnBattery)
    }
}
