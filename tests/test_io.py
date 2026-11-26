import sys, socket, threading, struct, time; sys.path[:0]=['.']
from rig import Transmitter
from rigcontrol import HamlibRig as RigctldRig
from gridtracker import GridTrackerLink
# --- mock rigctld (extended protocol) ---
log=[]
def srv():
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',45320)); s.listen(1)
    c,_=s.accept(); f=c.makefile('rwb',buffering=0)
    for line in f:
        l=line.decode().strip(); log.append(l)
        if l=='+f': f.write(b'get_freq:\nFrequency: 14040000\nRPRT 0\n')
        elif l=='+m': f.write(b'get_mode:\nMode: CW\nPassband: 500\nRPRT 0\n')
        else: f.write(f'{l[1:]}:\nRPRT 0\n'.encode())
threading.Thread(target=srv,daemon=True).start(); time.sleep(.2)
r=RigctldRig('127.0.0.1',45320)
print(r.get_freq(), r.get_mode(), r.set_cw_mode())
done=threading.Event()
t=Transmitter(on_done=done.set); t.configure(r, {'key_method':'rigkeyer','ptt_method':'VOX'}); t.wpm=40; t.send('TEST DE KO6EJV'); done.wait(10)
print(log)
# --- GridTracker packets ---
u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); u.bind(('127.0.0.1',22370)); u.settimeout(1)
g=GridTrackerLink('127.0.0.1',22370)
import datetime as dt
g.heartbeat(); g.status(14040000,'KO6EJV','DM12','W1AW','FN31'); g.decode('CQ W1AW FN31',5,700)
now=dt.datetime.now(dt.timezone.utc)
g.qso_logged(dict(call='W1AW',grid='FN31',freq=14040000,rst_sent='579',rst_rcvd='599',name='BOB',my_call='KO6EJV',my_grid='DM12LS',time_on=now,time_off=now))
for _ in range(4):
    p,_=u.recvfrom(4096); m,sch,typ=struct.unpack('>III',p[:12]); print(hex(m),sch,typ,len(p), p[12:40])
