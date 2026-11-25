using System.IO.Pipes;
using System.Net;
using System.Net.Sockets;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;

namespace Lianwang;

internal sealed class Clash(string sid) : IDisposable
{
    private readonly HttpClient http = new(new SocketsHttpHandler {
        AllowAutoRedirect = false, UseProxy = false, PooledConnectionLifetime = TimeSpan.FromSeconds(5),
        ConnectCallback = async (_, cancel) => {
            foreach (var channel in new[] { "production", "sidecar-release" }) {
                var pipe = new NamedPipeClientStream(".", $"verge-mihomo-{channel}-{State.Key(sid)}",
                    PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Anonymous);
                try { await pipe.ConnectAsync(350, cancel); return pipe; }
                catch (Exception e) when (e is IOException or TimeoutException or UnauthorizedAccessException) { pipe.Dispose(); }
            }
            throw new IOException("请先启动 Clash Verge Rev 2.5.10 或兼容版本。");
        }
    }) { BaseAddress = new Uri("http://localhost/"), Timeout = TimeSpan.FromSeconds(6), MaxResponseContentBufferSize = 4 << 20 };

    private async Task<JsonNode> Get(string path)
    {
        using var response = await http.GetAsync(path);
        response.EnsureSuccessStatusCode();
        return JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
    }
    internal async Task Check(bool proxy)
    {
        if ((string?)(await Get("configs"))["mode"] != "rule")
            throw new IOException("请在 Clash Verge 中选择「规则」模式。");
        var available = (await Get("providers/rules"))["providers"];
        if (available?["LW-Windows-direct"] is null || available["LW-Windows-proxy"] is null)
            throw new IOException("请先在 Clash Verge 重新应用一次当前订阅，使联网规则生效。");
        if (!proxy) return;
        var nodes = (await Get("proxies"))["proxies"];
        var node = nodes?["LW-Windows-Proxy"];
        string? choice = (string?)node?["now"];
        var type = choice == null ? null : (string?)nodes?[choice]?["type"];
        if (string.IsNullOrEmpty(type) || new[] {"Direct","Reject","RejectDrop","Pass","Compatible","DNS"}.Contains(type, StringComparer.OrdinalIgnoreCase))
            throw new IOException("当前订阅没有可用的代理节点。");
    }
    internal async Task Refresh(string mode)
    {
        using var response = await http.PutAsync("providers/rules/LW-Windows-" + mode, null);
        if (!response.IsSuccessStatusCode) throw new IOException($"Clash 规则更新失败（{(int)response.StatusCode}）。");
    }
    public void Dispose() => http.Dispose();
}

internal sealed class ProviderHost(string token) : IDisposable
{
    private readonly TcpListener listener = new(IPAddress.Loopback, 29841);
    private readonly CancellationTokenSource stop = new();
    private volatile Dictionary<string,string> snapshot = State.Empty();
    internal void Publish(Dictionary<string,string> rules) => snapshot = rules;
    internal void Start()
    {
        listener.Server.ExclusiveAddressUse = true;
        listener.Start(16);
        _ = Serve();
    }
    private async Task Serve()
    {
        while (!stop.IsCancellationRequested) {
            try {
                using var client = await listener.AcceptTcpClientAsync(stop.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(stop.Token);
                timeout.CancelAfter(2000);
                var stream = client.GetStream();
                var header = new byte[8192];
                int length = 0;
                string request = "";
                while (length < header.Length) {
                    int read = await stream.ReadAsync(header.AsMemory(length), timeout.Token);
                    if (read == 0) break;
                    length += read;
                    request = Encoding.ASCII.GetString(header, 0, length);
                    if (request.Contains("\r\n\r\n", StringComparison.Ordinal)) break;
                }
                var mode = new[] {"direct","proxy"}.FirstOrDefault(m => request.StartsWith($"GET /{token}/{m} HTTP/1.", StringComparison.Ordinal));
                byte[] body = mode == null ? [] : State.Payload(snapshot, mode);
                var status = mode == null ? "404 Not Found" : "200 OK";
                await stream.WriteAsync(Encoding.ASCII.GetBytes($"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n"), timeout.Token);
                await stream.WriteAsync(body, timeout.Token);
            } catch (Exception e) when (e is IOException or SocketException or OperationCanceledException or ObjectDisposedException) { }
        }
    }
    public void Dispose() { stop.Cancel(); listener.Stop(); }
}
