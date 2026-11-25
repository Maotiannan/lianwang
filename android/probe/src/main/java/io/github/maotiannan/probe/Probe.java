package io.github.maotiannan.probe;

import android.app.Service;
import android.content.Intent;
import android.os.*;
import java.net.*;
import java.io.*;

public class Probe extends Service {
    private final Messenger messenger=new Messenger(new Handler(Looper.getMainLooper(), msg->{
        ResultReceiver result=msg.getData().getParcelable("result");
        boolean hold=msg.getData().getBoolean("hold");
        if(result!=null) run(result,hold);
        return true;
    }));
    @Override public IBinder onBind(Intent intent){return messenger.getBinder();}
    private void run(ResultReceiver result,boolean hold){
        new Thread(()->{
            try {
                if(hold) {
                    try(Socket s=new Socket();DatagramSocket u=new DatagramSocket()){
                        InetAddress dest=InetAddress.getByName("192.0.2.81");
                        s.connect(new InetSocketAddress(dest,29185),6000);s.setSoTimeout(5000);
                        u.connect(dest,29185);u.setSoTimeout(5000);
                        for(int i=0;i<100;i++){
                            s.getOutputStream().write('L');
                            if(s.getInputStream().read()!='L')throw new IOException("tcp mismatch");
                            byte[] sent={'L',(byte)i};
                            u.send(new DatagramPacket(sent,sent.length));
                            byte[] data=new byte[2];u.receive(new DatagramPacket(data,data.length));
                            if(!java.util.Arrays.equals(sent,data)) throw new IOException("udp mismatch");
                            if(i==0)reply(result,"ready");
                            Thread.sleep(80);
                        }
                        reply(result,"done");
                    }
                } else {
                    try(Socket s=new Socket()){
                        s.connect(new InetSocketAddress("10.0.2.2",29184),6000);s.setSoTimeout(5000);
                        s.getOutputStream().write("GET / HTTP/1.0\r\n\r\n".getBytes("US-ASCII"));
                        BufferedReader lines=new BufferedReader(new InputStreamReader(s.getInputStream()));
                        String line,last="";
                        while((line=lines.readLine())!=null)last=line;
                        reply(result,last);
                    }
                }
            } catch(Exception e){reply(result,e.toString());}

        }).start();
    }
    private void reply(ResultReceiver r,String value){Bundle b=new Bundle();b.putString("value",value);r.send(0,b);}
}
