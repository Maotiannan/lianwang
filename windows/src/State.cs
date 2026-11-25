using Microsoft.Win32;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;

namespace Lianwang;

internal static class State
{
    internal const string RegistryPath = @"SOFTWARE\LianwangWindows";
    internal static readonly string[] Modes = ["auto", "direct", "proxy"];
    internal static readonly string[] Protected = ["Lianwang", "clash-verge", "verge-mihomo", "mihomo", "cloudflared", "com.docker", "dockerd", "svchost", "services", "lsass", "wininit", "System"];
    internal static string CurrentSid => WindowsIdentity.GetCurrent().User!.Value;
    internal static string PipeName(string sid) => "Lianwang.Windows." + sid;
    internal static string Key(string sid) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(sid)));
    internal static Dictionary<string,string> Empty() => new(StringComparer.OrdinalIgnoreCase);
    internal static bool IsProtected(string path)
    {
        var name = Path.GetFileNameWithoutExtension(path);
        return Protected.Any(p => name.Equals(p, StringComparison.OrdinalIgnoreCase) ||
            name.StartsWith(p + "-", StringComparison.OrdinalIgnoreCase) || name.StartsWith(p + ".", StringComparison.OrdinalIgnoreCase));
    }

    internal static string Validate(string? path, string? mode)
    {
        if (!Modes.Contains(mode) || path is null || path.Length < 7 || path.Length > 250 ||
            !char.IsAsciiLetter(path[0]) || path[1] != ':' || path[2] != '\\' || path[3..].Contains(':') ||
            path.IndexOfAny([';', ',', '*', '?', '"', '|', '<', '>']) >= 0 || path.Any(char.IsControl) ||
            !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(Path.GetFullPath(path), path, StringComparison.Ordinal) || IsProtected(path))
            throw new InvalidOperationException("请选择本机磁盘上的应用 .exe。系统、代理和服务保护项不可修改。");
        return path;
    }

    internal static Dictionary<string,string> Load(RegistryKey key)
    {
        var result = Empty();
        foreach (var (path, mode) in JsonSerializer.Deserialize<Dictionary<string,string>>((string?)key.GetValue("Rules") ?? "{}")!)
            if (mode != "auto") result.Add(Validate(path, mode), mode);
        return result;
    }

    internal static byte[] Payload(Dictionary<string,string> rules, string mode)
    {
        var lines = rules.Where(x => x.Value == mode).Select(x => "PROCESS-PATH," + x.Key).ToList();
        if (lines.Count == 0) lines.Add("PROCESS-PATH-REGEX,^$a");
        return Encoding.UTF8.GetBytes(string.Join("\n", lines) + "\n");
    }
}

internal sealed record Request(string Op, string? Path = null, string? Mode = null);
internal sealed record Reply(bool Ok, string Message = "", Dictionary<string,string>? Rules = null, string? Token = null);
