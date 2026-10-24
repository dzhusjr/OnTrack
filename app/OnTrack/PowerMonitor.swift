import Foundation
import IOKit.ps

/// Tells whether the Mac runs on battery and reports changes (plugging in / unplugging).
final class PowerMonitor {
    var onChange: ((Bool) -> Void)?  // true = on battery; main thread
    private var source: CFRunLoopSource?
    private var last: Bool?

    static var onBattery: Bool {
        guard let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let type = IOPSGetProvidingPowerSourceType(info)?.takeUnretainedValue() as String?
        else { return false }
        return type == kIOPSBatteryPowerValue
    }

    func start() {
        last = Self.onBattery
        let ctx = Unmanaged.passUnretained(self).toOpaque()
        source = IOPSNotificationCreateRunLoopSource({ ctx in
            guard let ctx else { return }
            let me = Unmanaged<PowerMonitor>.fromOpaque(ctx).takeUnretainedValue()
            let now = PowerMonitor.onBattery
            guard now != me.last else { return }  // the callback also fires for battery % changes
            me.last = now
            me.onChange?(now)
        }, ctx)?.takeRetainedValue()
        if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .defaultMode) }
    }
}
