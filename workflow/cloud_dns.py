"""Host-scoped DNS recovery; HTTPS still authenticates the requested hostname."""
import fcntl,ipaddress,json,secrets,socket,struct,time
from pathlib import Path
from .core.io import write_json


def answer(data,transaction,host):
    if len(data)<12:raise ValueError('Short DNS response')
    tid,flags,qd,an,_,_=struct.unpack('!6H',data[:12])
    if tid!=transaction or not flags&0x8000 or flags&0x020f or qd!=1:raise ValueError('Invalid DNS response')
    def name(offset,seen=()):
        if offset in seen or len(seen)>32:raise ValueError('DNS name cycle')
        labels=[];end=None;start=offset
        while True:
            n=data[offset];offset+=1
            if n==0:return '.'.join(labels),end or offset
            if n&0xc0==0xc0:
                pointer=((n&63)<<8)|data[offset];offset+=1
                suffix,_=name(pointer,seen+(start,));labels.append(suffix);return '.'.join(labels),end or offset
            if n>63:raise ValueError('Invalid DNS label')
            labels.append(data[offset:offset+n].decode('ascii').lower());offset+=n
    question,off=name(12)
    if question!=host or data[off:off+4]!=struct.pack('!HH',1,1):raise ValueError('DNS question mismatch')
    off+=4;records=[]
    for _ in range(an):
        owner,off=name(off);typ,cls,ttl,size=struct.unpack('!HHIH',data[off:off+10]);off+=10
        if off+size>len(data):raise ValueError('Truncated record')
        value=socket.inet_ntoa(data[off:off+4]) if typ==1 and size==4 else name(off)[0] if typ==5 else None
        records.append((owner,typ,cls,ttl,value));off+=size
    allowed={host};ttl=3600
    for _ in records:
        for owner,typ,cls,t,value in records:
            if owner in allowed and typ==5 and cls==1:allowed.add(value);ttl=min(ttl,t)
    for owner,typ,cls,t,value in records:
        if owner in allowed and typ==1 and cls==1 and ipaddress.ip_address(value).is_global:
            return {'hostname':host,'ipv4':value,'valid_until':time.time()+min(ttl,t,3600),'source':'scoped_recursive_dns'}
    raise ValueError('No public A answer')


def lookup(host,servers=('223.5.5.5','119.29.29.29')):
    tid=secrets.randbelow(65536)
    q=struct.pack('!6H',tid,0x100,1,0,0,0)+b''.join(bytes([len(x)])+x.encode('ascii') for x in host.split('.'))+b'\0'+struct.pack('!HH',1,1)
    for server in servers:
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
                s.settimeout(3);s.connect((server,53));s.send(q);return answer(s.recv(4096),tid,host)
        except (OSError,ValueError,IndexError,struct.error):continue
    raise socket.gaierror('Scoped DNS unavailable')


def install(host,cache_file):
    original=socket.getaddrinfo;cache_file=Path(cache_file)
    def resolve(requested,port,*args,**kwargs):
        try:return original(requested,port,*args,**kwargs)
        except socket.gaierror:
            if requested!=host:raise
            with cache_file.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                try:cached=json.loads(cache_file.read_text(encoding="utf-8"))
                except (FileNotFoundError,json.JSONDecodeError):cached={}
                if cached.get('hostname')!=host or time.time()>=cached.get('valid_until',0):
                    cached=lookup(host);write_json(cache_file,cached)
            return original(cached['ipv4'],port,*args,**kwargs)
    socket.getaddrinfo=resolve
    return original
