package io.github.maotiannan.lianwang;

import android.app.*;
import android.content.*;
import android.os.*;
import java.util.concurrent.*;

public class RoutingChecks extends Instrumentation {
    @Override public void onCreate(Bundle args){super.onCreate(args);start();}
    private LinkedBlockingQueue<String> probe(String pkg,boolean hold){
        var queue=new LinkedBlockingQueue<String>();
        var result=new ResultReceiver(new Handler(Looper.getMainLooper())){
            @Override protected void onReceiveResult(int code,Bundle data){queue.add(data.getString("value",""));}
        };
        var i=new Intent().setClassName(pkg,"io.github.maotiannan.probe.Probe")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("result",result).putExtra("hold",hold);
        getTargetContext().startActivity(i);return queue;
    }
    private void expect(LinkedBlockingQueue<String> q,String value) throws Exception {
        String got=q.poll(20,TimeUnit.SECONDS);
        if(!value.equals(got))throw new AssertionError("Expected "+value+", got "+got);
    }
    @Override public void onStart(){
        Context ctx=getTargetContext();
        var p=ctx.getSharedPreferences("routing",Context.MODE_PRIVATE);
        var end=new Bundle();
        String guard="io.github.maotiannan.probe.guard",target="io.github.maotiannan.probe.target";
        try {
            ctx.startActivity(new Intent(ctx,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            Thread.sleep(500);
            p.edit().clear().putString("proxy","socks5://10.0.2.2:29182").putString("app."+guard,"proxy").commit();
            ctx.startForegroundService(new Intent(ctx,RoutingService.class));
            for(int n=0;n<80&&!RoutingService.active;n++)Thread.sleep(100);
            if(!RoutingService.active)throw new AssertionError("VPN failed: "+RoutingService.message);
            var ongoing=probe(guard,true); expect(ongoing,"ready");
            for(String mode:new String[]{"auto","proxy","direct","proxy","auto"}){
                p.edit().putString("app."+target,mode).commit();
                expect(probe(target,false),mode.equals("proxy")?"PROXY":"LOCAL");
            }
            expect(ongoing,"done");
            end.putString("result","passed real app UID routing and continuous TCP/UDP sockets");
            finish(Activity.RESULT_OK,end);
        } catch(Throwable e){end.putString("failure",e.toString());finish(Activity.RESULT_CANCELED,end);}
        finally {ctx.stopService(new Intent(ctx,RoutingService.class));}
    }
}
