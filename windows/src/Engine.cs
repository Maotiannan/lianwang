using Microsoft.Win32;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text.Json;
using System.Security.Cryptography;

namespace Lianwang;

internal static class Wire
{
    internal static async Task Write<T>(Stream stream, T value, CancellationToken ct)
    {
        byte[] body = JsonSerializer.SerializeToUtf8Bytes(value);
        await stream.WriteAsync(BitConverter.GetBytes(body.Length), ct);
        await stream.WriteAsync(body, ct);
    }
    internal static async Task<T> Read<T>(Stream stream, int limit, CancellationToken ct)
    {
        byte[] size = new byte[4];
        await stream.ReadExactlyAsync(size, ct);
        int length = BitConverter.ToInt32(size);
        if (length <= 0 || length > limit) throw new IOException("请求长度无效。");
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, ct);
        return JsonSerializer.Deserialize<T>(body) ?? throw new IOException("请求格式无效。");
    }
    internal static async Task<Reply> Call(Request request)
    {
        using var pipe = new NamedPipeClientStream(".", State.PipeName(State.CurrentSid), PipeDirection.InOut,
            PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
        using var ct = new CancellationTokenSource(45000);
        await pipe.ConnectAsync(1800, ct.Token);
        await Write(pipe, request, ct.Token);
        return await Read<Reply>(pipe, 1 << 20, ct.Token);
    }
}

internal sealed class Engine : ServiceBase
{
    private readonly CancellationTokenSource stop = new();
    private readonly Native driver = new();
    private readonly SemaphoreSlim gate = new(1);
    private RegistryKey key = null!;
    private string sid = "", token = "", error = "";
    private bool loaded, reconcile;
    private ProviderHost host = null!;
    private Clash clash = null!;
    private Dictionary<string,string> rules = State.Empty();
    internal Engine() { ServiceName = "LianwangWindows"; }
    protected override void OnStart(string[] args)
    {
        key = Registry.LocalMachine.OpenSubKey(State.RegistryPath, writable:true)!;
        sid = (string)key.GetValue("Owner")!;
        token = (string?)key.GetValue("Token") ?? Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
        key.SetValue("Token", token);
        rules = State.Load(key);
        host = new(token);
        clash = new(sid);
        host.Publish(rules);
        try {
            host.Start();
            driver.Start(token);
            foreach (var item in rules) driver.Apply(item.Key, item.Value);
            loaded = true;
            reconcile = rules.Count != 0 || (int?)key.GetValue("Pending") == 1;
        } catch (Exception e) { error = e.Message; }
        _ = Listen();
        _ = RecoverLoop();
    }
    private async Task RecoverLoop()
    {
        while (!stop.IsCancellationRequested) {
            await gate.WaitAsync();
            try {
                if (loaded && reconcile) await Recover();
                else if (loaded && rules.ContainsValue("proxy")) await clash.FollowSelection();
            } catch { /* Clash may start after login or after the service. */ }
            finally { gate.Release(); }
            try { await Task.Delay(5000, stop.Token); } catch (OperationCanceledException) { break; }
        }
    }
    private async Task Recover()
    {
        await clash.Check(false);
        host.Publish(rules);
        await clash.Refresh("direct");
        await clash.Refresh("proxy");
        key.SetValue("Pending", 0);
        reconcile = false;
    }
    private async Task<Reply> Handle(Request req)
    {
        if (req.Op == "setup") return new(true, Token:token);
        if (req.Op == "list") return new(true, !loaded ? error : reconcile ? "等待 Clash 同步已保存的规则" : "", rules);
        if (req.Op != "set") return new(false, "未知操作。");
        if (!loaded) return new(false, error);
        string mode = req.Mode!, path = State.Validate(req.Path, mode);
        string oldMode = rules.GetValueOrDefault(path, "auto");
        if (reconcile) await Recover();
        if (oldMode == mode) return new(true, Rules:rules);
        await clash.Check(mode == "proxy");
        if (mode != "auto" && rules.Count >= 512 && !rules.ContainsKey(path)) throw new IOException("已达到 512 个手动应用上限。");
        var next = new Dictionary<string,string>(rules, StringComparer.OrdinalIgnoreCase);
        if (mode == "auto") next.Remove(path); else next[path] = mode;
        var changed = new[]{mode, oldMode}.Where(m => m != "auto").Distinct().ToArray();
        key.SetValue("Pending", 1);
        try {
            host.Publish(next);
            foreach (string m in changed) await clash.Refresh(m);
            driver.Apply(path, mode);
            key.SetValue("Rules", JsonSerializer.Serialize(next));
            rules = next;
        } catch {
            host.Publish(rules);
            driver.Apply(path, oldMode);
            reconcile = true;
            try { await Recover(); } catch { }
            throw;
        }
        key.SetValue("Pending", 0);
        return new(true, Rules:rules);
    }
    private async Task Listen()
    {
        var acl = new PipeSecurity();
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null), PipeAccessRights.FullControl, AccessControlType.Deny));
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(sid), PipeAccessRights.ReadWrite, AccessControlType.Allow));
        acl.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User!, PipeAccessRights.FullControl, AccessControlType.Allow));
        while (!stop.IsCancellationRequested) {
            try {
                using var pipe = NamedPipeServerStreamAcl.Create(State.PipeName(sid), PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.FirstPipeInstance, 0, 0, acl);
                await pipe.WaitForConnectionAsync(stop.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(stop.Token);
                timeout.CancelAfter(45000);
                var request = await Wire.Read<Request>(pipe, 4096, timeout.Token);
                Reply reply;
                await gate.WaitAsync(timeout.Token);
                try { reply = await Handle(request); }
                catch (Exception e) { reply = new(false, e is HttpRequestException ? "无法连接 Clash Verge，请确认已启动并应用联网规则。" : e.Message); }
                finally { gate.Release(); }
                await Wire.Write(pipe, reply, timeout.Token);
            } catch (OperationCanceledException) { }
              catch (Exception) { await Task.Delay(150); }
        }
    }
    protected override void OnStop()
    {
        stop.Cancel();
        gate.Wait();
        try { driver.Dispose(); host.Dispose(); clash.Dispose(); key.Dispose(); }
        finally { gate.Release(); }
    }
}
