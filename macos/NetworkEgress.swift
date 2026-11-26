import AppKit
import SwiftUI

enum ConnectionMode: String, Decodable {
    case auto, direct, proxy
    var label: String {
        switch self { case .auto: return "自动"; case .direct: return "直连"; case .proxy: return "代理" }
    }
    var explanation: String {
        switch self {
        case .auto: return "自动 · 按 Clash 规则"
        case .direct: return "直接连接互联网"
        case .proxy: return "代理 · 跟随当前订阅"
        }
    }
}
struct SavedApp: Decodable { let path: String; let mode: ConnectionMode }
struct MenuSnapshot: Decodable {
    let apps: [SavedApp]
    let status: String
    let ready: Bool
    let paused: Bool
    let pending: Bool
    let protectedPaths: [String]
    let warning: String?
}
struct AppRow: Identifiable {
    let path: String
    let name: String
    let mode: ConnectionMode
    var id: String { path }
}
struct BackendMessage: Decodable { let error: String? }
struct LocalFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

func backend(_ args: [String]) throws -> Data {
    guard let script = Bundle.main.url(forResource: "backend", withExtension: "py") else {
        throw LocalFailure(message: "找不到网络组件，请重新安装应用。")
    }
    let task = Process()
    let output = Pipe()
    task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    task.arguments = ["-B", script.path] + args
    task.standardOutput = output
    task.standardError = FileHandle.nullDevice
    try task.run()
    let data = output.fileHandleForReading.readDataToEndOfFile()
    task.waitUntilExit()
    if task.terminationStatus != 0 {
        let detail = (try? JSONDecoder().decode(BackendMessage.self, from: data))?.error
        throw LocalFailure(message: detail ?? "网络组件暂未响应，请重试。")
    }
    return data
}

@MainActor
final class Store: ObservableObject {
    @Published var snapshot: MenuSnapshot?
    @Published var apps: [AppRow] = []
    @Published var query = ""
    @Published var savingPath: String?
    @Published var feedback: String?
    @Published var problem: String?
    @Published var backgroundProblem: String?
    @Published var pickingApp = false
    @Published var scrollTarget: String?
    private var installedApps: [AppRow] = []
    private var added = Set(UserDefaults.standard.stringArray(forKey: "addedApplications") ?? [])
    private var reading = false
    private var syncing = false
    private var visibleTimer: Timer?
    private var systemTimer: Timer?
    private let hiddenNames = Set(["MClash.app", "Clash Verge.app", "网络出口.app", "联网.app"])

    var busy: Bool { savingPath != nil }
    var ready: Bool { snapshot?.ready == true }
    var filteredApps: [AppRow] {
        (pickingApp ? installedApps : apps).filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }
    }
    var statusText: String {
        guard let snapshot else { return "正在准备" }
        if snapshot.paused { return "已暂停" }
        if snapshot.status == "upgrade-pending" { return "更新待完成" }
        return snapshot.ready ? "已就绪" : "需要连接"
    }
    var unavailableText: String? {
        guard let status = snapshot?.status else { return nil }
        if status == "ready" { return snapshot?.warning }
        switch status {
        case "paused": return "已暂停单独设置，应用按原来的方式联网。"
        case "first-run": return "请打开下载包里的「首次启用.command」。"
        case "clash-off": return "Clash Verge 未运行，暂时无法设置。"
        case "clash-mode": return "Clash Verge 当前不在规则模式，暂时无法设置。"
        case "approval", "reboot": return "后台需要维护，请让助手检查。"
        case "connecting": return "正在连接，请稍等。"
        case "recovery": return "上次切换未完成，请让助手恢复设置。"
        case "upgrade-pending": return "当前连接保持不变，更新完成后即可切换。"
        default: return "连接暂不可用，请让助手检查。"
        }
    }
    var problemDetails: String? { problem ?? backgroundProblem }
    var problemText: String? {
        guard let problem = problemDetails else { return nil }
        if problem.contains("尚待恢复") { return "设置尚未恢复，请让助手检查。" }
        if problem.contains("服务可达性") { return "网站连接暂不稳定，已保留原设置。" }
        if problem.contains("选好代理线路") { return "请先在 Clash 中选好代理线路。" }
        if problem.contains("承载本机") { return "这个应用正在承载网站，已保留原联网方式。" }
        return "操作未完成，请稍后重试或让助手检查。"
    }

    func start() {
        Task { await repairMenuVisibility(); refresh() }
        systemTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.repairMenuVisibility()
                self?.syncSystem()
            }
        }
        systemTimer?.tolerance = 3
    }
    func repairMenuVisibility() async {
        do {
            _ = try await Task.detached { try backend(["repair-menu"]) }.value
        } catch {
            NSLog("联网：菜单栏显示未能恢复：%@", error.localizedDescription)
        }
    }
    func visible(_ shown: Bool) {
        visibleTimer?.invalidate()
        visibleTimer = nil
        if shown {
            refresh()
            visibleTimer = Timer.scheduledTimer(withTimeInterval: 6, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.refresh() }
            }
        }
    }
    private func readSnapshot() async throws -> MenuSnapshot {
        try await Task.detached {
            try JSONDecoder().decode(MenuSnapshot.self, from: backend(["menu-snapshot"]))
        }.value
    }
    func refresh() {
        guard !reading && !busy else { return }
        reading = true
        Task {
            defer { reading = false }
            do {
                snapshot = try await readSnapshot()
                rebuildList()
            } catch { problem = error.localizedDescription }
            await repairMenuVisibility()
        }
    }
    private func syncSystem() {
        guard !syncing && !busy && snapshot?.paused != true else { return }
        syncing = true
        Task {
            defer { syncing = false }
            do {
                _ = try await Task.detached { try backend(["sync-system"]) }.value
                if snapshot?.pending == true {
                    _ = try await Task.detached { try backend(["reconcile"]) }.value
                }
                backgroundProblem = nil
            } catch { backgroundProblem = error.localizedDescription }
        }
    }
    private func allowed(_ path: String) -> Bool {
        let url = URL(fileURLWithPath: path)
        return url.pathExtension.lowercased() == "app"
            && !hiddenNames.contains(url.lastPathComponent)
            && !(snapshot?.protectedPaths ?? ["/Applications/Docker.app"]).contains(path)
            && FileManager.default.fileExists(atPath: path)
    }
    private func rebuildList() {
        let running = NSWorkspace.shared.runningApplications.filter {
            $0.activationPolicy == .regular && $0.bundleURL?.path.hasPrefix("/System/") != true
        }
        let saved = Dictionary(uniqueKeysWithValues: (snapshot?.apps ?? []).map { ($0.path, $0.mode) })
        var paths = added.union(saved.keys)
        running.compactMap(\.bundleURL).forEach { paths.insert($0.path) }
        apps = paths.filter(allowed).map { path in
            let name = running.first(where: { $0.bundleURL?.path == path })?.localizedName
                ?? (FileManager.default.displayName(atPath: path) as NSString).deletingPathExtension
            return AppRow(path: path, name: name, mode: saved[path] ?? .auto)
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    private func keepListed(_ path: String) {
        added.insert(path)
        UserDefaults.standard.set(added.sorted(), forKey: "addedApplications")
    }
    func pickApp() {
        pickingApp.toggle()
        query = ""
        guard pickingApp else { return }
        var paths = Set(apps.map(\.path))
        let roots = ["/Applications", "/System/Applications", NSHomeDirectory() + "/Applications"]
        for root in roots {
            guard let enumerator = FileManager.default.enumerator(at: URL(fileURLWithPath: root),
                includingPropertiesForKeys: nil, options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { continue }
            for case let url as URL in enumerator where url.pathExtension.lowercased() == "app" {
                if allowed(url.path) { paths.insert(url.path) }
            }
        }
        installedApps = paths.map { path in
            AppRow(path: path, name: (FileManager.default.displayName(atPath: path) as NSString).deletingPathExtension, mode: .auto)
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    func addApp(_ path: String) {
        guard allowed(path) else { return }
        keepListed(path)
        pickingApp = false
        query = ""
        rebuildList()
        scrollTarget = path
    }
    func set(_ mode: ConnectionMode, for app: AppRow) {
        guard ready && !busy && app.mode != mode else { return }
        keepListed(app.path)
        savingPath = app.path
        feedback = nil
        problem = nil
        Task {
            do {
                _ = try await Task.detached { try backend(["set-simple", app.path, mode.rawValue]) }.value
                snapshot = try await readSnapshot()
                rebuildList()
                feedback = mode == .auto ? "\(app.name) 已恢复自动" : "\(app.name) 已切换为\(mode.label)"
            } catch { problem = error.localizedDescription }
            savingPath = nil
        }
    }
}

struct Panel: View {
    @ObservedObject var model: Store
    private var version: String { Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "" }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if let text = model.unavailableText { unavailable(text) }
            HStack(spacing: 9) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索应用", text: $model.query).textFieldStyle(.plain)
                Button { model.pickApp() } label: {
                    Label(model.pickingApp ? "返回" : "添加", systemImage: model.pickingApp ? "chevron.left" : "plus")
                }.buttonStyle(.borderless).disabled(model.busy)
                    .accessibilityLabel(model.pickingApp ? "返回应用列表" : "添加应用")
            }.padding(10).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 9))
                .padding(.horizontal, 16).padding(.bottom, 10)
            Divider()
            appList
            Divider()
            footer
        }
        .frame(width: 430, height: 520)
        .background(Color(nsColor: .windowBackgroundColor))
    }
    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 6) {
                Text("应用联网").font(.system(size: 18, weight: .semibold))
                Text(model.pickingApp ? "选择要添加的应用，默认使用自动。" : "默认按 Clash 规则，需要时再单独指定。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 5) {
                Circle().fill(model.ready ? Color.green : Color.orange).frame(width: 6, height: 6)
                Text(model.statusText).font(.system(size: 11))
            }.padding(.top, 4).foregroundStyle(.secondary)
        }.padding(16)
    }
    private func unavailable(_ text: String) -> some View {
        HStack {
            Text(text).font(.caption).fixedSize(horizontal: false, vertical: true)
        }.padding(10).background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 16).padding(.bottom, 10).disabled(model.busy)
    }
    private var appList: some View {
        ScrollViewReader { scroll in
            ScrollView {
                LazyVStack(spacing: 0) {
                    if model.filteredApps.isEmpty {
                        VStack(spacing: 9) {
                            Image(systemName: "app.dashed").font(.system(size: 25)).foregroundStyle(.tertiary)
                            Text(model.snapshot == nil ? "正在读取应用…" : "没有找到应用")
                                .font(.system(size: 13, weight: .medium))
                            Text("点上方「添加」，选择一个应用。").font(.caption).foregroundStyle(.secondary)
                        }.padding(.top, 48).frame(maxWidth: .infinity)
                    }
                    ForEach(model.filteredApps) { app in
                        if model.pickingApp { addRow(app).id(app.id) }
                        else { appRow(app).id(app.id) }
                    }
                }.padding(.vertical, 4).id(model.pickingApp)
            }.onChange(of: model.scrollTarget) { _, path in
                if let path { scroll.scrollTo(path, anchor: .center) }
            }
        }.frame(maxHeight: .infinity)
    }
    private func addRow(_ app: AppRow) -> some View {
        Button { model.addApp(app.path) } label: {
            HStack(spacing: 10) {
                Image(nsImage: NSWorkspace.shared.icon(forFile: app.path)).resizable().frame(width: 28, height: 28)
                Text(app.name).font(.system(size: 13)).lineLimit(1)
                Spacer()
                Image(systemName: "plus.circle").foregroundStyle(Color.accentColor)
            }.padding(.horizontal, 16).padding(.vertical, 10).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel("添加 \(app.name)")
    }
    private func appRow(_ app: AppRow) -> some View {
        HStack(spacing: 10) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: app.path))
                .resizable().interpolation(.high).frame(width: 32, height: 32).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(app.name).font(.system(size: 13, weight: .medium)).lineLimit(1).help(app.name)
                if model.savingPath == app.path {
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.mini)
                        Text("正在切换…").font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                } else {
                    Text(app.mode.explanation).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 2) {
                modeButton(.auto, app: app)
                modeButton(.direct, app: app)
                modeButton(.proxy, app: app)
            }.padding(3).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(.quaternary, lineWidth: 0.5))
        }.padding(.horizontal, 16).padding(.vertical, 10)
    }
    private func modeButton(_ mode: ConnectionMode, app: AppRow) -> some View {
        let selected = app.mode == mode
        return Button { model.set(mode, for: app) } label: {
            Text(mode.label).font(.system(size: 12, weight: selected ? .semibold : .regular))
                .frame(width: 51, height: 28)
                .foregroundStyle(selected ? Color.white : Color.primary)
                .background(selected ? Color.accentColor : Color.clear, in: RoundedRectangle(cornerRadius: 6))
                .contentShape(RoundedRectangle(cornerRadius: 6))
        }.buttonStyle(.plain).disabled(model.busy || !model.ready)
            .accessibilityLabel("\(app.name) · \(mode.label)")
            .accessibilityValue(selected ? "已选择" : "未选择")
    }
    private var footer: some View {
        VStack(alignment: .leading, spacing: 7) {
            if let message = model.problemText {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                    Text(message).frame(maxWidth: .infinity, alignment: .leading).lineLimit(2)
                    Button { model.problem = nil; model.backgroundProblem = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel("关闭提示")
                }.font(.caption)
            } else if let feedback = model.feedback {
                Label(feedback, systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(.green).lineLimit(1)
            }
            HStack(spacing: 5) {
                Label("网站服务已保护", systemImage: "lock.shield").help("Docker、公网隧道和系统 DNS 保留原来的联网方式。")
                Spacer()
                Text("v\(version)")
                Text("切换对新连接生效")
                Button { NSApp.terminate(nil) } label: {
                    Label("退出联网", systemImage: "power")
                }.buttonStyle(.borderless).font(.system(size: 11))
                    .keyboardShortcut("q", modifiers: .command)
                    .accessibilityLabel("退出联网")
                    .help("关闭菜单栏工具；当前联网设置和代理后台继续运行。")
            }.font(.system(size: 10)).foregroundStyle(.secondary)
        }.padding(.horizontal, 16).padding(.vertical, 12)
    }
}

@MainActor
final class Delegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    let store = Store()
    let popover = NSPopover()
    var item: NSStatusItem!
    func applicationDidFinishLaunching(_ notification: Notification) {
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
        if others.contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            NSApp.terminate(nil); return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.autosaveName = "NetworkEgress"
        item.isVisible = true
        if let button = item.button {
            button.title = " 联网"
            button.image = NSImage(systemSymbolName: "arrow.triangle.branch", accessibilityDescription: "应用联网")
            button.image?.isTemplate = true
            button.imagePosition = .imageLeft
            button.toolTip = "应用联网 · 直连或代理"
            button.setAccessibilityLabel("应用联网")
            button.target = self
            button.action = #selector(toggle)
        }
        popover.behavior = .transient
        popover.delegate = self
        popover.contentSize = NSSize(width: 430, height: 520)
        popover.contentViewController = NSHostingController(rootView: Panel(model: store))
        store.start()
        if CommandLine.arguments.contains("--show") { show() }
    }
    func show() {
        Task {
            await store.repairMenuVisibility()
            item.isVisible = true
            guard !popover.isShown, let button = item.button else { return }
            NSApp.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
            store.visible(true)
        }
    }
    @objc func toggle() {
        if popover.isShown { popover.performClose(nil) } else { show() }
    }
    func popoverDidClose(_ notification: Notification) { store.visible(false) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        show(); return true
    }
}
@main
struct NetworkEgress {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let delegate = Delegate()
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}
