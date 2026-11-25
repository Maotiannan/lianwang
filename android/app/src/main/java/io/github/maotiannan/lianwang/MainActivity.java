package io.github.maotiannan.lianwang;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.net.VpnService;
import android.os.*;
import android.text.*;
import android.view.*;
import android.widget.*;
import java.util.*;
import core.Core;

public final class MainActivity extends Activity {
    private SharedPreferences prefs;
    private LinearLayout screen,settings;
    private TextView status;
    private EditText proxy,automatic,search;
    private Button start;
    private ListView list;
    private final List<Item> apps=new ArrayList<>(), visible=new ArrayList<>();
    private final Rows adapter=new Rows();
    private record Item(String name,String pkg,int uid) {}
    private final BroadcastReceiver updates=new BroadcastReceiver() {
        public void onReceive(Context c,Intent i){refresh();}
    };
    private int dp(int v){return Math.round(v*getResources().getDisplayMetrics().density);}
    private TextView text(String value,int size){TextView t=new TextView(this);t.setText(value);t.setTextSize(size);return t;}
    private Button button(String label,View.OnClickListener action){Button b=new Button(this);b.setText(label);b.setOnClickListener(action);b.setAllCaps(false);return b;}
    private EditText field(String hint,String value){EditText e=new EditText(this);e.setSingleLine();e.setHint(hint);e.setText(value);return e;}
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved); prefs=getSharedPreferences("routing",MODE_PRIVATE);
        screen=new LinearLayout(this);screen.setOrientation(LinearLayout.VERTICAL);
        screen.setPadding(dp(14),dp(10),dp(14),dp(6));setContentView(screen);
        screen.setOnApplyWindowInsetsListener((v,insets)->{
            v.setPadding(dp(14),dp(10)+insets.getSystemWindowInsetTop(),dp(14),dp(6)+insets.getSystemWindowInsetBottom());return insets;
        });
        var head=new LinearLayout(this);head.setGravity(Gravity.CENTER_VERTICAL);screen.addView(head);
        head.addView(text("联网",24),new LinearLayout.LayoutParams(0,-2,1));
        head.addView(button("出口设置",v->settings.setVisibility(settings.getVisibility()==View.VISIBLE?View.GONE:View.VISIBLE)));
        start=button("开启",v->toggle());head.addView(start);
        status=text("",13);screen.addView(status);
        settings=new LinearLayout(this);settings.setOrientation(LinearLayout.VERTICAL);screen.addView(settings);
        settings.addView(text("代理出口 · SOCKS5",14));
        proxy=field("socks5://主机:端口",prefs.getString("proxy",""));settings.addView(proxy);
        settings.addView(text("自动模式的规则入口（可留空）",14));
        automatic=field("留空使用原网络",prefs.getString("automatic",""));settings.addView(automatic);
        settings.addView(button("保存出口",v->save()));
        settings.setVisibility(prefs.getString("proxy","").isEmpty()?View.VISIBLE:View.GONE);
        search=field("搜索应用","");screen.addView(search);
        list=new ListView(this);screen.addView(list,new LinearLayout.LayoutParams(-1,0,1));list.setAdapter(adapter);
        search.addTextChangedListener(new TextWatcher(){
            public void beforeTextChanged(CharSequence s,int st,int c,int a){}
            public void afterTextChanged(Editable e){}
            public void onTextChanged(CharSequence s,int st,int before,int count){filter();}
        });
        new Thread(()->{
            PackageManager pm=getPackageManager();
            var found=new HashMap<String,Item>();
            for(var r:pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),0)){
                var a=r.activityInfo.applicationInfo;
                if(!getPackageName().equals(a.packageName)) found.put(a.packageName,new Item(a.loadLabel(pm).toString(),a.packageName,a.uid));
            }
            var sorted=new ArrayList<>(found.values());
            sorted.sort(Comparator.comparing(Item::name,String.CASE_INSENSITIVE_ORDER));
            runOnUiThread(()->{if(!isDestroyed()){apps.addAll(sorted);filter();}});
        }).start();
    }
    private boolean save(){
        String f=proxy.getText().toString().trim(),a=automatic.getText().toString().trim();
        try {Core.validateEndpoint(f);Core.validateEndpoint(a);}
        catch(Exception e){status.setText(e.getMessage());return false;}
        if(f.isEmpty()){status.setText("请填写代理出口");return false;}
        prefs.edit().putString("proxy",f).putString("automatic",a).apply();
        settings.setVisibility(View.GONE);refresh();return true;
    }
    private void toggle(){
        if(RoutingService.active){stopService(new Intent(this,RoutingService.class));return;}
        if(settings.getVisibility()==View.VISIBLE && !save())return;
        Intent ask=VpnService.prepare(this);
        if(ask!=null) startActivityForResult(ask,20); else begin();
    }
    private void begin(){startForegroundService(new Intent(this,RoutingService.class));}
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);if(request==20 && result==RESULT_OK)begin();
    }
    @Override protected void onStart(){
        super.onStart();var filter=new IntentFilter(getPackageName()+".STATE");
        if(Build.VERSION.SDK_INT>=33) registerReceiver(updates,filter,RECEIVER_NOT_EXPORTED);else registerReceiver(updates,filter);
        refresh();
    }
    @Override protected void onStop(){unregisterReceiver(updates);super.onStop();}
    private void refresh(){
        start.setText(RoutingService.active?"关闭":"开启");
        String message=RoutingService.message;
        status.setText(message!=null && !message.isEmpty()?message:RoutingService.active?"分流已开启 · 修改对新连接生效":"分流已关闭");
    }
    private void filter(){
        String q=search.getText().toString().trim().toLowerCase(Locale.ROOT);
        visible.clear();for(Item item:apps) if((item.name+" "+item.pkg).toLowerCase(Locale.ROOT).contains(q))visible.add(item);
        adapter.notifyDataSetChanged();
    }
    private final class Rows extends BaseAdapter {
        public int getCount(){return visible.size();}
        public Item getItem(int index){return visible.get(index);}
        public long getItemId(int index){return index;}
        public View getView(int index,View reuse,ViewGroup parent){
            var item=getItem(index);var row=new LinearLayout(MainActivity.this);row.setGravity(Gravity.CENTER_VERTICAL);
            var name=text(item.name,15);name.setMaxLines(2);name.setContentDescription(item.name+" "+item.pkg);
            row.addView(name,new LinearLayout.LayoutParams(0,-2,1));
            String mode=RoutingService.policy(prefs,getPackageManager(),item.uid);
            String[] modes={"auto","direct","proxy"},labels={"自动","直连","代理"};
            for(int i=0;i<modes.length;i++){
                String m=modes[i];
                Button b=button((mode.equals(m)?"✓":"")+labels[i],v->{
                    if(m.equals("proxy") && prefs.getString("proxy","").isEmpty()){settings.setVisibility(View.VISIBLE);status.setText("请先填写代理出口");return;}
                    var edit=prefs.edit();String[] peers=getPackageManager().getPackagesForUid(item.uid);
                    if(peers==null) peers=new String[]{item.pkg};
                    for(String pkg:peers){if(m.equals("auto"))edit.remove("app."+pkg);else edit.putString("app."+pkg,m);}
                    edit.apply();notifyDataSetChanged();
                });
                b.setMinWidth(0);b.setMinimumWidth(0);b.setPadding(0,0,0,0);b.setTextSize(13);
                row.addView(b,new LinearLayout.LayoutParams(dp(56),dp(48)));
            }
            return row;
        }
    }
}
