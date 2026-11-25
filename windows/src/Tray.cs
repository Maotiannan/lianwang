using System.Diagnostics;

namespace Lianwang;

internal sealed class Tray : ApplicationContext
{
    private readonly NotifyIcon icon;
    private readonly PanelForm panel = new();
    internal Tray()
    {
        icon = new NotifyIcon { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath),
            Text = "联网 · 应用网络出口", Visible = true };
        icon.MouseClick += async (_, e) => {
            if (e.Button is not (MouseButtons.Left or MouseButtons.Right)) return;
            if (panel.Visible) { panel.Hide(); return; }
            var area = Screen.FromPoint(Cursor.Position).WorkingArea;
            panel.Location = new Point(Math.Clamp(Cursor.Position.X - panel.Width / 2, area.Left, Math.Max(area.Left, area.Right - panel.Width)),
                Math.Clamp(Cursor.Position.Y - panel.Height, area.Top, Math.Max(area.Top, area.Bottom - panel.Height)));
            panel.Show();
            panel.Activate();
            await panel.Reload();
        };
        panel.Quit += ExitThread;
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { icon.Visible = false; icon.Dispose(); panel.Dispose(); }
        base.Dispose(disposing);
    }
}

internal sealed class PanelForm : Form
{
    private readonly TextBox search = new() { PlaceholderText="搜索应用，或粘贴 .exe 完整路径", Dock=DockStyle.Fill };
    private readonly DataGridView grid = new() { Dock=DockStyle.Fill, ReadOnly=true, AllowUserToAddRows=false, AllowUserToDeleteRows=false,
        AllowUserToResizeRows=false, RowHeadersVisible=false, MultiSelect=false, SelectionMode=DataGridViewSelectionMode.FullRowSelect,
        BackgroundColor=SystemColors.Window, BorderStyle=BorderStyle.None, AutoSizeRowsMode=DataGridViewAutoSizeRowsMode.None };
    private readonly Label status = new() { Dock=DockStyle.Fill, AutoEllipsis=true, TextAlign=ContentAlignment.MiddleLeft };
    private Dictionary<string,string> rules = State.Empty();
    private SortedSet<string> paths = new(StringComparer.OrdinalIgnoreCase);
    private bool busy;
    internal event Action? Quit;
    internal PanelForm()
    {
        Text = "联网"; ShowInTaskbar=false; TopMost=true; FormBorderStyle=FormBorderStyle.FixedToolWindow;
        StartPosition=FormStartPosition.Manual; ClientSize=new Size(494,438); AutoScaleMode=AutoScaleMode.Dpi;
        Font=new Font("Microsoft YaHei UI", 9);
        var layout = new TableLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(12), ColumnCount=1, RowCount=3 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute,36));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute,46));
        layout.Controls.Add(search,0,0); layout.Controls.Add(grid,0,1);
        var footer = new TableLayoutPanel{Dock=DockStyle.Fill, ColumnCount=2, RowCount=1};
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,84));
        var exit = new Button {Text="退出托盘", Dock=DockStyle.Fill};
        exit.Click += (_,_) => Quit?.Invoke();
        footer.Controls.Add(status,0,0); footer.Controls.Add(exit,1,0); layout.Controls.Add(footer,0,2);
        Controls.Add(layout);
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText="应用", AutoSizeMode=DataGridViewAutoSizeColumnMode.Fill });
        foreach(var label in new[]{"自动","直连","代理"})
            grid.Columns.Add(new DataGridViewButtonColumn { HeaderText=label, Width=62, SortMode=DataGridViewColumnSortMode.NotSortable });
        grid.RowTemplate.Height=31;
        grid.CellContentClick += async (_,e) => {
            if (busy || e.RowIndex < 0 || e.ColumnIndex < 1) return;
            string path=(string)grid.Rows[e.RowIndex].Tag!;
            busy=true; grid.Enabled=false;
            try {
                var r=await Wire.Call(new("set",path,State.Modes[e.ColumnIndex-1]));
                if (!r.Ok) throw new IOException(r.Message);
                SetRules(r); Render(); status.Text="已保存，下次新建连接时生效。";
            } catch(Exception ex) { status.Text=ex.Message; }
            finally { busy=false; grid.Enabled=true; }
        };
        search.TextChanged += (_,_) => Render();
        KeyPreview=true;
        KeyDown += (_,e) => { if(e.KeyCode == Keys.Escape) Hide(); };
        FormClosing += (_,e) => { if(e.CloseReason == CloseReason.UserClosing) {e.Cancel=true; Hide();} };
        Deactivate += (_,_) => Hide();
    }
    private void SetRules(Reply r) { rules=new(r.Rules ?? State.Empty(),StringComparer.OrdinalIgnoreCase); }
    internal async Task Reload()
    {
        if(busy) return;
        busy=true; grid.Enabled=false; status.Text="正在读取应用…";
        try {
            var result=await Wire.Call(new("list"));
            SetRules(result);
            paths=await Task.Run(() => {
                var items=new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
                using var self=Process.GetCurrentProcess();
                int session=self.SessionId;
                foreach(var proc in Process.GetProcesses()) using(proc) {
                    try {
                        if(proc.SessionId != session) continue;
                        var p=proc.MainModule?.FileName;
                        if(p!=null && !State.IsProtected(p)) items.Add(p);
                    } catch(Exception e) when(e is System.ComponentModel.Win32Exception or InvalidOperationException or NotSupportedException) { }
                }
                return items;
            });
            Render();
            var modified=await Program.Prepare();
            status.Text=result.Message.Length>0 ? result.Message : modified ? "请在 Clash Verge 重新应用一次当前订阅。" : "自动跟随系统和 Clash 规则";
        } catch(Exception e) { status.Text=e is TimeoutException ? "后台未响应，请确认联网服务正在运行。" : e.Message; }
        finally { busy=false; grid.Enabled=true; }
    }
    private void Render()
    {
        string text=search.Text.Trim().Trim('"');
        var list=paths.Concat(rules.Keys).Distinct(StringComparer.OrdinalIgnoreCase)
            .Where(p => p.Contains(text,StringComparison.OrdinalIgnoreCase)).ToList();
        if(text.EndsWith(".exe",StringComparison.OrdinalIgnoreCase) && Path.IsPathFullyQualified(text) &&
            File.Exists(text) && !list.Contains(text,StringComparer.OrdinalIgnoreCase) && !State.IsProtected(text)) list.Add(text);
        grid.Rows.Clear();
        foreach(var p in list.OrderByDescending(rules.ContainsKey).ThenBy(Path.GetFileName,StringComparer.OrdinalIgnoreCase)) {
            string mode=rules.GetValueOrDefault(p,"auto");
            int row=grid.Rows.Add(Path.GetFileNameWithoutExtension(p),mode=="auto"?"✓ 自动":"自动",mode=="direct"?"✓ 直连":"直连",mode=="proxy"?"✓ 代理":"代理");
            grid.Rows[row].Tag=p; grid.Rows[row].Cells[0].ToolTipText=p;
        }
        grid.ClearSelection();
    }
}
