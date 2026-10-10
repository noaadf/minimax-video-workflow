import socket,struct,time,unittest
from workflow.cloud_dns import answer
class DnsTests(unittest.TestCase):
    def packet(self,ip='47.102.128.206'):
        name=b'\x04open\x08bigmodel\x02cn\0'
        return struct.pack('!6H',17,0x8180,1,1,0,0)+name+struct.pack('!HH',1,1)+b'\xc0\x0c'+struct.pack('!HHIH',1,1,60,4)+socket.inet_aton(ip)
    def test_compressed_answer_and_ttl(self):
        r=answer(self.packet(),17,'open.bigmodel.cn')
        self.assertEqual(r['ipv4'],'47.102.128.206');self.assertLessEqual(r['valid_until'],time.time()+60)
    def test_reject_wrong_identity_and_private_ip(self):
        for data,tid,host in [(self.packet(),18,'open.bigmodel.cn'),(self.packet(),17,'other.test'),(self.packet('127.0.0.1'),17,'open.bigmodel.cn')]:
            with self.assertRaises(ValueError):answer(data,tid,host)
