using Microsoft.Win32;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Reflection;

namespace Lianwang;

internal static class Program
{
    private const string Begin = "// >>> Lianwang Windows managed v1 >>>";
    private const string End = "// <<< Lianwang Windows managed v1 <<<";
    private static readonly string Hook = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "io.github.clash-verge-rev.clash-verge-rev","profiles","Script.js");

    [STAThread]
    static int Main(string[] args)
    {
        if (args is ["--service"]) { ServiceBase.Run(new Engine()); return 0; }
        try {
            if (args is ["--owner"]) {
                using var k = Registry.LocalMachine.CreateSubKey(State.RegistryPath);
                var old = (string?)k.GetValue("Owner");
                if (old != null && old != State.CurrentSid) throw new IOException("请由原安装账户更新联网。");
                var acl = new RegistrySecurity();
                acl.SetAccessRuleProtection(true, false);
                foreach (var id in new[]{WellKnownSidType.BuiltinAdministratorsSid, WellKnownSidType.LocalSystemSid})
                    acl.AddAccessRule(new RegistryAccessRule(new SecurityIdentifier(id,null), RegistryRights.FullControl, AccessControlType.Allow));
                k.SetAccessControl(acl);
                k.SetValue("Owner",State.CurrentSid);
                return 0;
            }
            if (args is ["--remove-hook"]) { UpdateHook(null); return 0; }
            if (args is ["--prepare"]) { Prepare().GetAwaiter().GetResult(); return 0; }
            if (args is ["--list"] || args is ["--set",_,_]) {
                var reply = Wire.Call(args.Length == 1 ? new("list") : new("set",args[1],args[2])).GetAwaiter().GetResult();
                Console.WriteLine(JsonSerializer.Serialize(reply));
                return reply.Ok ? 0 : 1;
            }
            if (args.Length != 0) return 2;
            using var once = new Mutex(true, @"Local\Lianwang.Tray." + State.CurrentSid, out var first);
            if (!first) return 0;
            ApplicationConfiguration.Initialize();
            using var tray = new Tray();
            Application.Run(tray);
            return 0;
        } catch (Exception e) { Console.Error.WriteLine(e.Message); return 1; }
    }

    internal static async Task<bool> Prepare()
    {
        var response = await Wire.Call(new("setup"));
        if (!response.Ok || response.Token?.Length != 64 || !response.Token.All(char.IsAsciiHexDigit))
            throw new IOException("联网后台尚未就绪。");
        return UpdateHook(response.Token);
    }

    private static bool UpdateHook(string? token)
    {
        if (!File.Exists(Hook)) {
            if (token == null) return false;
            throw new IOException("请先启动 Clash Verge Rev 并导入订阅，再打开联网托盘。");
        }
        byte[] original = File.ReadAllBytes(Hook);
        var encoding = new UTF8Encoding(false, true);
        string script = encoding.GetString(original);
        int start = script.IndexOf(Begin, StringComparison.Ordinal);
        if (start >= 0) {
            int end = script.IndexOf(End, start, StringComparison.Ordinal);
            if (end < 0) throw new IOException("现有联网脚本标记不完整，请检查 Clash 全局扩展脚本。");
            if (start > 0 && script[start-1] == '\n') start--;
            script = script.Remove(start, end + End.Length - start);
        }
        if (token != null) {
            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Lianwang.enhancer.js")!;
            using var reader = new StreamReader(stream);
            script += "\n" + Begin + "\n" + reader.ReadToEnd() +
                "\nconst lwUserMain = main;\nmain = function(config, name) { return lianwangConfig(lwUserMain(config, name), '" + token + "'); };\n" + End;
        }
        byte[] bytes = encoding.GetBytes(script);
        if (bytes.SequenceEqual(original)) return false;
        string temp = Hook + "." + Guid.NewGuid().ToString("N");
        try {
            File.WriteAllBytes(temp,bytes);
            if (!File.ReadAllBytes(Hook).SequenceEqual(original)) throw new IOException("Clash 扩展脚本刚被编辑，请重试。");
            File.Replace(temp,Hook,null);
        } finally { File.Delete(temp); }
        return true;
    }
}
