using System;
using System.Net.Sockets;
using System.IO;
using System.Threading;

class Probe {
    static void Main(string[] args) {
        using (var tcp = new TcpClient()) using(var udp = new UdpClient()) {
            udp.Client.ReceiveTimeout=5000;
            udp.Connect("192.0.2.81", 29185);
            if(!tcp.ConnectAsync("192.0.2.81",29185).Wait(5000)) throw new IOException("TCP timed out");
            var stream=tcp.GetStream(); stream.ReadTimeout=5000;
            int count = args.Length==0 ? 1 : 1200;
            for(int i=0; i<count; i++) {
                byte[] payload={(byte)'L',(byte)i};
                stream.Write(payload,0,2);
                if(stream.ReadByte()!=payload[0] || stream.ReadByte()!=payload[1]) throw new IOException("TCP echo mismatch");
                udp.Send(payload,2);
                System.Net.IPEndPoint from=null;
                byte[] got=udp.Receive(ref from);
                if(got.Length!=2 || got[0]!=payload[0] || got[1]!=payload[1]) throw new IOException("UDP echo mismatch");
                if(args.Length>0) {
                    if(i==0) File.WriteAllText(args[0]+".ready","ready");
                    if(File.Exists(args[0]+".done")) return;
                    Thread.Sleep(50);
                }
            }
            if(args.Length>0) throw new IOException("test never completed");
        }
    }
}
