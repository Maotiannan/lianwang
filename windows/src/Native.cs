using System.Runtime.InteropServices;

namespace Lianwang;

internal sealed class Native : IDisposable
{
    private const string Dll = "ProxyBridgeCore.dll";
    private readonly Dictionary<string,uint> ids = new(StringComparer.OrdinalIgnoreCase);
    private uint proxy;
    private bool started;

    [DllImport(Dll)] private static extern uint ProxyBridge_AddProxyConfig(int type, string host, ushort port, string user, string password, bool domain);
    [DllImport(Dll)] private static extern bool ProxyBridge_Start();
    [DllImport(Dll)] private static extern bool ProxyBridge_Stop();
    [DllImport(Dll)] private static extern uint ProxyBridge_AddRule([MarshalAs(UnmanagedType.LPUTF8Str)] string path, string hosts, string ports, string domains, int protocols, int action, uint config, bool loopback);
    [DllImport(Dll)] private static extern bool ProxyBridge_DeleteRule(uint id);

    internal void Start(string token)
    {
        // Upstream reports a driver handle independently of its relay bind results.
        // Refuse collisions before opening the interception handle.
        var network = System.Net.NetworkInformation.IPGlobalProperties.GetIPGlobalProperties();
        if (network.GetActiveTcpListeners().Any(e => e.Port == 34010) ||
            network.GetActiveUdpListeners().Any(e => e.Port == 34011))
            throw new IOException("转发端口 34010/TCP 或 34011/UDP 已被其他程序占用。");
        proxy = ProxyBridge_AddProxyConfig(1, "127.0.0.1", 29842, "lw", token, false);
        if (proxy == 0 || !ProxyBridge_Start())
            throw new InvalidOperationException("WinDivert 驱动启动失败，请检查 Windows 安全中心和应用兼容性。");
        started = true;
    }

    internal void Apply(string path, string mode)
    {
        // Direct uses the native socket for transparent traffic. Its explicit HTTP proxy requests
        // are routed DIRECT by the Mihomo provider. Automatic removes both overrides.
        if (mode == "proxy" && !ids.ContainsKey(path))
        {
            uint id = ProxyBridge_AddRule(path, "*", "*", "*", 2, 0, proxy, false);
            if (id == 0) throw new IOException("驱动未接受应用规则。");
            ids.Add(path, id);
        }
        else if (mode != "proxy" && ids.TryGetValue(path, out var id))
        {
            if (!ProxyBridge_DeleteRule(id)) throw new IOException("驱动未移除应用规则。");
            ids.Remove(path);
        }
    }

    public void Dispose() { if (started) ProxyBridge_Stop(); }
}
