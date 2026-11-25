package io.github.maotiannan.lianwang;

import android.app.*;
import android.content.*;
import android.net.*;
import android.os.ParcelFileDescriptor;
import android.system.OsConstants;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.io.IOException;
import core.Core;
import core.Session;

public final class RoutingService extends VpnService implements SharedPreferences.OnSharedPreferenceChangeListener {
    static volatile boolean active;
    static volatile String message = "";
    private Session session;
    private ParcelFileDescriptor tun;
    private SharedPreferences prefs;
    private ConnectivityManager network;

    @Override public void onCreate() {
        super.onCreate();
        prefs = getSharedPreferences("routing", MODE_PRIVATE);
        network = getSystemService(ConnectivityManager.class);
        prefs.registerOnSharedPreferenceChangeListener(this);
    }
    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (intent != null && "stop".equals(intent.getAction())) { stopSelf(); return START_NOT_STICKY; }
        if (session != null) return START_NOT_STICKY;
        var nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("vpn","应用分流",NotificationManager.IMPORTANCE_LOW));
        var open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
        startForeground(1,new Notification.Builder(this,"vpn").setSmallIcon(R.drawable.arrow)
            .setContentTitle("联网").setContentText("正在按应用分流").setContentIntent(open).build());
        try {
            Builder builder=new Builder().setSession("联网").setMtu(1500)
                .addAddress("10.87.0.2",32).addAddress("fd5e:1a7::2",128)
                .addRoute("0.0.0.0",0).addRoute("::",0).addDisallowedApplication(getPackageName());
            // Leave DNS selection to Android's underlying network.
            tun=builder.establish();
            if(tun==null) throw new IOException("请先允许 VPN 连接");
            session=Core.start(tun.getFd(),this::mode,prefs.getString("proxy",""),prefs.getString("automatic",""));
            active=true; message="";
        } catch(Exception e) { message=e.getMessage(); stopSelf(); }
        notifyUi(); return START_NOT_STICKY;
    }
    private String mode(long protocol,String source,long port,String dest,long remote) {
        try {
            var local=new InetSocketAddress(InetAddress.getByName(source),(int)port);
            var peer=new InetSocketAddress(InetAddress.getByName(dest),(int)remote);
            int uid=network.getConnectionOwnerUid((int)protocol,local,peer);
            if(uid<0 && protocol==OsConstants.IPPROTO_UDP) {
                // Unconnected UDP sockets may have a wildcard peer.
                String any=dest.contains(":")?"::":"0.0.0.0";
                uid=network.getConnectionOwnerUid((int)protocol,local,new InetSocketAddress(any,0));
            }
            if(uid<0) return "auto";
            return policy(prefs,getPackageManager(),uid);
        } catch(RuntimeException | java.net.UnknownHostException e) { return "auto"; }
    }
    static String policy(SharedPreferences p,android.content.pm.PackageManager pm,int uid) {
        String[] apps=pm.getPackagesForUid(uid);
        if(apps!=null) for(String pkg:apps) {
            String mode=p.getString("app."+pkg,null);
            if(mode!=null) return mode;
        }
        return "auto";
    }
    @Override public void onSharedPreferenceChanged(SharedPreferences p,String key) {
        if(session!=null && ("proxy".equals(key) || "automatic".equals(key))) {
            try { session.configure(p.getString("proxy",""),p.getString("automatic","")); }
            catch(Exception e) { message=e.getMessage(); notifyUi(); }
        }
    }
    private void notifyUi() { sendBroadcast(new Intent(getPackageName()+".STATE").setPackage(getPackageName())); }
    @Override public void onRevoke() { stopSelf(); }
    @Override public void onDestroy() {
        prefs.unregisterOnSharedPreferenceChangeListener(this);
        if(session!=null) {session.close(); session=null;}
        if(tun!=null) try {tun.close();} catch(IOException ignored) {}
        active=false; notifyUi(); stopForeground(STOP_FOREGROUND_REMOVE); super.onDestroy();
    }
}
