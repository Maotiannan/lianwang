import AppKit

// Launch the signed vendor app as a helper, without reopening its UI or
// registering it in macOS's recent-app list.
guard CommandLine.arguments.count == 2 else { exit(64) }
let target = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
guard Bundle(url: target)?.bundleIdentifier == "one.leaper.mclash" else { exit(66) }
let running = NSRunningApplication.runningApplications(withBundleIdentifier: "one.leaper.mclash")
if running.contains(where: { $0.bundleURL?.standardizedFileURL == target }) { exit(0) }

guard running.isEmpty else {
    fputs("已有其他位置的 MClash 在运行。请先确认其连接可以结束，再退出它。\n", stderr)
    exit(69)
}

let configuration = NSWorkspace.OpenConfiguration()
configuration.activates = false
configuration.hides = true
configuration.addsToRecentItems = false
configuration.allowsRunningApplicationSubstitution = false
configuration.arguments = ["--mclash-background"]
var result: Int32?
NSWorkspace.shared.openApplication(at: target, configuration: configuration) { _, error in
    if error != nil { fputs("内置后台未能启动。\n", stderr) }
    result = error == nil ? 0 : 1
}
let deadline = Date(timeIntervalSinceNow: 15)
while result == nil && Date() < deadline {
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
}
exit(result ?? 1)
